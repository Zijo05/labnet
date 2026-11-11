"""Integration tests for milestone 4: every scenario, solved end to end.

For each built-in scenario this:

1. starts it and confirms the checks do NOT all pass (so they test something),
2. applies the written reference solution from docs/solutions/, block by block,
3. confirms every check passes,
4. restarts the whole lab and confirms every check still passes, which proves
   the solution is saved in configuration files and not just in memory.

The nat, firewall and troubleshoot scenarios need the host to be online.
"""

from __future__ import annotations

import time

import pytest

from labnet import engine, incus, scenario
from tests.solutions import solution_steps

from .conftest import labnet, on_node, remove_lab

pytestmark = pytest.mark.integration

SCENARIOS = ["static", "routing", "dhcp", "nat", "dns", "firewall", "ospf", "troubleshoot"]


def failures(results: list[scenario.CheckResult]) -> list[str]:
    return [f"{r.check.name}: {r.detail}" for r in results if not r.passed]


def apply_solution(item: scenario.Scenario) -> None:
    lab = engine.get_lab(item.lab)
    for node, script in solution_steps(item.number, item.name):
        result = incus.exec_in(lab.node(node).instance, ["sh", "-es"], stdin=script, timeout=180)
        assert result.returncode == 0, (
            f"solution step on {node} failed:\n{script}\n{result.stdout}{result.stderr}"
        )


@pytest.mark.parametrize("name", SCENARIOS)
def test_reference_solution_passes_the_checks(name: str) -> None:
    remove_lab(name)
    try:
        started = labnet("scenario", "start", name)
        assert started.returncode == 0, started.stdout + started.stderr
        item = scenario.find_active(name)
        assert f"# Scenario {item.number}:" in started.stdout  # the task is shown

        # 1. Unsolved, the scenario must not pass.
        before = scenario.run_checks(item, patient=False)
        assert failures(before), "every check passes before anything was configured"

        # 2 + 3. The documented solution makes every check pass.
        apply_solution(item)
        after = scenario.run_checks(item)
        assert not failures(after), failures(after)

        # The CLI agrees and exits with 0.
        checked = labnet("scenario", "check", "--scenario", name)
        assert checked.returncode == 0, checked.stdout
        assert f"All {len(item.checks)} checks passed" in checked.stdout

        # 4. The solution survives a restart of every node.
        engine.stop(name)
        engine.start(name)
        rebooted = scenario.run_checks(item)
        assert not failures(rebooted), f"after restart: {failures(rebooted)}"
    finally:
        remove_lab(name)


def test_ospf_reroutes_around_a_failed_link() -> None:
    """The 'break it' half of the OSPF scenario: pull a cable, traffic finds another way."""
    remove_lab("ospf")
    try:
        item = scenario.start("ospf")
        apply_solution(item)
        assert not failures(scenario.run_checks(item))

        direct = on_node("ospf/pc1", "traceroute -n -w 1 -q 1 10.0.3.10").stdout
        assert "10.0.13.2" in direct  # straight from r1 to r3

        assert labnet("link", "down", "ospf/r1", "net13").returncode == 0
        started = time.monotonic()
        recovered = on_node(
            "ospf/pc1",
            "i=0; until ping -c 1 -W 1 10.0.3.10; do i=$((i+1)); [ $i -ge 30 ] && exit 1; done",
        )
        assert recovered.returncode == 0, "no connectivity 30 seconds after the link failed"
        assert time.monotonic() - started < 15, (
            "reconvergence took longer than the tuned timers allow"
        )

        detour = on_node("ospf/pc1", "traceroute -n -w 1 -q 1 10.0.3.10").stdout
        assert "10.0.12.2" in detour and "10.0.23.2" in detour  # now via r2
        assert "10.0.13.2" not in detour

        assert labnet("link", "up", "ospf/r1", "net13").returncode == 0
        back = on_node(
            "ospf/pc1",
            "i=0; until traceroute -n -w 1 -q 1 10.0.3.10 | grep -q 10.0.13.2; "
            "do i=$((i+1)); [ $i -ge 30 ] && exit 1; sleep 1; done",
        )
        assert back.returncode == 0, "the direct path did not come back"
    finally:
        remove_lab("ospf")


def test_scenario_hint_reset_and_list() -> None:
    remove_lab("static")
    try:
        assert labnet("scenario", "check").returncode == 1  # nothing is running
        assert labnet("scenario", "start", "static").returncode == 0

        listing = labnet("scenario", "list").stdout
        assert "running" in next(line for line in listing.splitlines() if " static " in line)

        again = labnet("scenario", "start", "static")
        assert again.returncode == 1 and "already exists" in again.stderr

        first = labnet("scenario", "hint")
        assert "Hint 1 of 3" in first.stdout and "labnet scenario hint 2" in first.stdout
        assert "Hint 3 of 3" in labnet("scenario", "hint", "3").stdout
        assert labnet("scenario", "hint", "9").returncode == 1

        failing = labnet("scenario", "check")
        assert failing.returncode == 1
        assert "FAIL" in failing.stdout and "0 of 4 checks passed" in failing.stdout

        # Reset throws away what was done and gives a clean lab back.
        assert on_node("static/pc1", "touch /root/mine").returncode == 0
        assert labnet("scenario", "reset").returncode == 1  # needs --yes without a terminal
        assert labnet("scenario", "reset", "--yes").returncode == 0
        assert on_node("static/pc1", "test -e /root/mine").returncode == 1
        assert engine.get_lab("static").scenario == "static"
    finally:
        remove_lab("static")
