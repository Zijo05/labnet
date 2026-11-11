"""Guided scenarios with automatic checks.

A scenario is a directory with four things:

    scenario.yaml   title, hints, checks and optional setup scripts
    topology.yaml   the lab to build (a normal topology file)
    task.md         what the learner should do
    setup/*.sh      optional scripts run on nodes right after the lab is built
                    (used to pre-configure, or deliberately break, a network)

Checks are declarative. Each one names a node and one of three things to
test there, so writing a new scenario needs no Python:

    - name: pc1 has an address in the office network
      node: pc1
      address_in: 192.168.10.0/24

    - name: guests cannot reach the office LAN
      node: pc3
      ping: 192.168.10.11
      expect: fail

    - name: the name www resolves
      node: pc1
      run: dig +short www.office.internal
      output_contains: 192.168.10.5
      wait: 20            # keep trying for up to 20 seconds
      explain: Shown to the learner when the check fails.

Built-in scenarios live next to this file in ``scenarios/``. A scenario can
also be given as a path to a directory.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from labnet import engine, incus
from labnet.errors import LabnetError
from labnet.topology import Topology, load_topology

BUILTIN_DIR = Path(__file__).parent / "scenarios"

Reporter = Callable[[str], None]

_CHECK_KINDS = ("address_in", "ping", "run")
_CHECK_KEYS = (
    "name",
    "node",
    *_CHECK_KINDS,
    "expect",
    "exit_code",
    "output_contains",
    "output_matches",
    "wait",
    "explain",
)
_SCENARIO_KEYS = ("title", "number", "summary", "setup", "hints", "checks")


def _quiet(_message: str) -> None:
    """Default reporter: say nothing."""


class ScenarioError(LabnetError):
    """A scenario directory is missing something or its YAML is wrong."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"The scenario in {source} has problems:\n  " + "\n  ".join(problems),
            hint="See docs/scenarios.md for the scenario file format.",
        )


@dataclass
class Check:
    """One automatic check: something to test on one node."""

    name: str
    node: str
    kind: str  # address_in / ping / run
    target: str  # the subnet, the ping destination, or the command
    expect_success: bool = True
    exit_code: int = 0
    output_contains: str | None = None
    output_matches: str | None = None
    wait: int = 0  # seconds to keep retrying before giving up
    explain: str = ""


@dataclass
class CheckResult:
    """The outcome of one check."""

    check: Check
    passed: bool
    detail: str  # what was actually observed


@dataclass
class Scenario:
    """A loaded, validated scenario."""

    name: str  # directory name, e.g. "nat"
    reference: str  # what identifies it later: the name, or an absolute path
    directory: Path
    number: int
    title: str
    summary: str
    topology: Topology
    task: str  # Markdown
    hints: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    setup: dict[str, Path] = field(default_factory=dict)  # node name -> script

    @property
    def lab(self) -> str:
        """Name of the lab this scenario builds."""
        return self.topology.name


# --- loading ---------------------------------------------------------------


def list_scenarios() -> list[Scenario]:
    """All built-in scenarios, easiest first."""
    scenarios = [
        load_scenario(path.name)
        for path in sorted(BUILTIN_DIR.iterdir())
        if (path / "scenario.yaml").is_file()
    ]
    return sorted(scenarios, key=lambda s: (s.number, s.name))


def load_scenario(reference: str) -> Scenario:
    """Load a scenario by built-in name or by directory path."""
    if "/" in reference or Path(reference).is_dir():
        directory = Path(reference).resolve()
        stored_reference = str(directory)
    else:
        directory = BUILTIN_DIR / reference
        stored_reference = reference
    if not (directory / "scenario.yaml").is_file():
        names = ", ".join(p.name for p in sorted(BUILTIN_DIR.iterdir()) if p.is_dir())
        raise LabnetError(
            f"There is no scenario called '{reference}'.",
            hint=f"Built-in scenarios: {names}. Or give the path of a scenario directory.",
        )

    source = f"{directory.name}/scenario.yaml"
    try:
        data = yaml.safe_load((directory / "scenario.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(source, [f"not valid YAML: {exc}"]) from None
    if not isinstance(data, dict):
        raise ScenarioError(
            source, ["the file must be a mapping with at least 'title' and 'checks'"]
        )

    problems: list[str] = []
    for key in data:
        if key not in _SCENARIO_KEYS:
            problems.append(f"unknown key '{key}'; allowed keys: {', '.join(_SCENARIO_KEYS)}")

    for required in ("topology.yaml", "task.md"):
        if not (directory / required).is_file():
            problems.append(f"the file {required} is missing from the scenario directory")
    if problems:
        raise ScenarioError(source, problems)

    topology = load_topology(directory / "topology.yaml")
    checks = _parse_checks(data.get("checks"), topology, problems)
    setup = _parse_setup(data.get("setup"), topology, directory, problems)
    hints = data.get("hints") or []
    if not isinstance(hints, list) or not all(isinstance(hint, str) for hint in hints):
        problems.append("'hints' must be a list of text items")
        hints = []
    if not isinstance(data.get("title"), str):
        problems.append("missing 'title'")
    if problems:
        raise ScenarioError(source, problems)

    return Scenario(
        name=directory.name,
        reference=stored_reference,
        directory=directory,
        number=int(data.get("number", 99)),
        title=data["title"],
        summary=str(data.get("summary", "")),
        topology=topology,
        task=(directory / "task.md").read_text(encoding="utf-8"),
        hints=hints,
        checks=checks,
        setup=setup,
    )


def _parse_checks(value: Any, topology: Topology, problems: list[str]) -> list[Check]:
    if not isinstance(value, list) or not value:
        problems.append("'checks' must be a non-empty list")
        return []
    checks = []
    for index, item in enumerate(value, start=1):
        where = f"check {index}"
        if not isinstance(item, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        if isinstance(item.get("name"), str):
            where = f"check '{item['name']}'"
        else:
            problems.append(f"{where}: missing 'name'")
        for key in item:
            if key not in _CHECK_KEYS:
                problems.append(f"{where}: unknown key '{key}'")

        node = item.get("node")
        if node not in topology.nodes:
            problems.append(
                f"{where}: node '{node}' is not in the topology "
                f"(nodes: {', '.join(topology.nodes)})"
            )
        kinds = [kind for kind in _CHECK_KINDS if kind in item]
        if len(kinds) != 1:
            problems.append(f"{where}: needs exactly one of: {', '.join(_CHECK_KINDS)}")
            continue
        kind = kinds[0]
        target = str(item[kind])
        if kind == "address_in":
            try:
                ipaddress.ip_network(target)
            except ValueError:
                problems.append(f"{where}: '{target}' is not a network like 192.168.10.0/24")
        if item.get("expect", "success") not in ("success", "fail"):
            problems.append(f"{where}: 'expect' must be 'success' or 'fail'")
        if item.get("output_matches") is not None:
            try:
                re.compile(str(item["output_matches"]))
            except re.error as exc:
                problems.append(f"{where}: 'output_matches' is not a valid pattern: {exc}")

        checks.append(
            Check(
                name=str(item.get("name", where)),
                node=str(node),
                kind=kind,
                target=target,
                expect_success=item.get("expect", "success") == "success",
                exit_code=int(item.get("exit_code", 0)),
                output_contains=_optional_text(item.get("output_contains")),
                output_matches=_optional_text(item.get("output_matches")),
                wait=int(item.get("wait", 0)),
                explain=str(item.get("explain", "")).strip(),
            )
        )
    return checks


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _parse_setup(
    value: Any, topology: Topology, directory: Path, problems: list[str]
) -> dict[str, Path]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append("'setup' must map node names to script files")
        return {}
    setup = {}
    for node, script in value.items():
        if node not in topology.nodes:
            problems.append(f"setup: node '{node}' is not in the topology")
        path = directory / str(script)
        if not path.is_file():
            problems.append(f"setup: script '{script}' for node '{node}' does not exist")
        setup[str(node)] = path
    return setup


# --- which scenario is running ---------------------------------------------


def active_scenarios() -> dict[str, str]:
    """Labs that belong to a scenario, as ``{lab name: scenario reference}``."""
    return {lab.name: lab.scenario for lab in engine.read_labs().values() if lab.scenario}


def find_active(name: str | None = None) -> Scenario:
    """Return the running scenario, or the one called ``name``.

    With no name given there must be exactly one scenario running.
    """
    active = active_scenarios()
    if name:
        scenario = load_scenario(name)
        if scenario.lab not in active:
            raise LabnetError(
                f"The scenario '{scenario.name}' has not been started.",
                hint=f"Start it with: labnet scenario start {name}",
            )
        return scenario
    if not active:
        raise LabnetError(
            "No scenario is running.",
            hint="See the choices with 'labnet scenario list' and start one with "
            "'labnet scenario start <name>'.",
        )
    if len(active) > 1:
        names = ", ".join(Path(ref).name for ref in active.values())
        raise LabnetError(
            "More than one scenario is running.",
            hint=f"Say which one you mean with --scenario. Running: {names}.",
        )
    return load_scenario(next(iter(active.values())))


# --- start / reset ---------------------------------------------------------


def start(reference: str, report: Reporter = _quiet) -> Scenario:
    """Build a scenario's lab and run its setup scripts."""
    scenario = load_scenario(reference)
    if scenario.lab in engine.read_labs():
        raise LabnetError(
            f"A lab called '{scenario.lab}' already exists, so the scenario cannot start.",
            hint=f"Continue with it ('labnet scenario check'), start over with "
            f"'labnet scenario reset', or remove it with 'labnet down {scenario.lab}'.",
        )
    engine.up(scenario.topology, report=report, scenario=scenario.reference)
    _run_setup(scenario, report)
    return scenario


def reset(name: str | None = None, report: Reporter = _quiet) -> Scenario:
    """Throw the scenario's lab away and build it fresh."""
    scenario = find_active(name)
    report(f"Removing lab '{scenario.lab}'")
    engine.down(scenario.lab, report=report)
    report(f"Building lab '{scenario.lab}' again")
    engine.up(scenario.topology, report=report, scenario=scenario.reference)
    _run_setup(scenario, report)
    return scenario


def _run_setup(scenario: Scenario, report: Reporter) -> None:
    """Run each node's setup script inside the node."""
    lab = engine.get_lab(scenario.lab)
    for node_name, script in scenario.setup.items():
        report(f"  node {node_name}: preparing")
        node = lab.node(node_name)
        result = incus.exec_in(
            node.instance, ["sh", "-es"], stdin=script.read_text(encoding="utf-8"), timeout=180
        )
        if result.returncode != 0:
            raise LabnetError(
                f"The setup script for node '{node_name}' failed:\n"
                f"{(result.stdout + result.stderr).strip()[-600:]}",
                hint="This is a problem in the scenario, not something you did. "
                f"Remove the lab with 'labnet down {scenario.lab}' and report it.",
            )


# --- checks ----------------------------------------------------------------


def run_checks(
    scenario: Scenario, patient: bool = True, report: Callable[[CheckResult], None] | None = None
) -> list[CheckResult]:
    """Run every check of a scenario against its lab.

    With ``patient=True`` a check that sets ``wait`` is retried until it
    passes or its time is up (leases and routing protocols need a moment).
    ``report`` is called with each result as soon as it is known.
    """
    lab = engine.get_lab(scenario.lab)
    results = []
    for check in scenario.checks:
        node = lab.node(check.node)
        if not node.running:
            result = CheckResult(check, False, f"node {check.node} is stopped")
        else:
            result = _run_check(check, node.instance, patient)
        results.append(result)
        if report:
            report(result)
    return results


def _run_check(check: Check, instance: str, patient: bool) -> CheckResult:
    """Evaluate one check, retrying while it fails if the check allows waiting."""
    # Waiting only makes sense when success is expected: "must fail" is
    # either true right away or the learner has more work to do.
    deadline = time.monotonic() + (check.wait if patient and check.expect_success else 0)
    while True:
        passed, detail = _evaluate(check, instance)
        if passed or time.monotonic() >= deadline:
            return CheckResult(check, passed, detail)
        time.sleep(2)


def _evaluate(check: Check, instance: str) -> tuple[bool, str]:
    """Run the check once. Returns (passed, what was observed)."""
    if check.kind == "address_in":
        return _evaluate_address(check, instance)
    if check.kind == "ping":
        result = incus.exec_in(instance, ["ping", "-c", "2", "-W", "2", check.target], timeout=30)
        reachable = result.returncode == 0
        detail = f"{check.target} answered" if reachable else f"no answer from {check.target}"
        return reachable == check.expect_success, detail
    return _evaluate_command(check, instance)


def _evaluate_address(check: Check, instance: str) -> tuple[bool, str]:
    subnet = ipaddress.ip_network(check.target)
    result = incus.exec_in(instance, ["ip", "-j", "-4", "addr", "show", "scope", "global"])
    try:
        interfaces = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False, "could not read the node's addresses"
    addresses = [
        f"{info['local']}/{info['prefixlen']}"
        for interface in interfaces
        for info in interface.get("addr_info", [])
    ]
    inside = [a for a in addresses if ipaddress.ip_interface(a).ip in subnet]
    if inside:
        return check.expect_success, f"has {', '.join(inside)}"
    found = f"its addresses are: {', '.join(addresses)}" if addresses else "it has no IPv4 address"
    return not check.expect_success, found


def _evaluate_command(check: Check, instance: str) -> tuple[bool, str]:
    result = incus.exec_in(instance, ["sh", "-c", check.target], timeout=60)
    output = (result.stdout + result.stderr).strip()
    shown = _shorten(output) or "(no output)"

    if not check.expect_success:
        failed = result.returncode != 0
        return failed, f"the command {'failed' if failed else 'succeeded'}: {shown}"
    if result.returncode != check.exit_code:
        return False, f"exit code {result.returncode}: {shown}"
    if check.output_contains is not None and check.output_contains not in output:
        return False, f"expected to see '{check.output_contains}', got: {shown}"
    if check.output_matches is not None and not re.search(check.output_matches, output, re.S):
        return False, f"output did not match what was expected: {shown}"
    return True, shown


def _shorten(text: str, limit: int = 200) -> str:
    """Collapse output to one line of at most ``limit`` characters."""
    one_line = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."
