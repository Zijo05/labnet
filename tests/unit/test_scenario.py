"""Unit tests for scenario loading and check evaluation. No Incus needed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tests.solutions import solution_file, solution_steps

from labnet import scenario
from labnet.errors import LabnetError
from labnet.scenario import Check, ScenarioError

EXPECTED = ["static", "routing", "dhcp", "nat", "dns", "firewall", "ospf", "troubleshoot"]


def test_all_builtin_scenarios_load_in_order() -> None:
    scenarios = scenario.list_scenarios()
    assert [s.name for s in scenarios] == EXPECTED
    assert [s.number for s in scenarios] == list(range(1, 9))
    for item in scenarios:
        assert item.lab == item.name, "the lab is named after the scenario"
        assert item.title and item.summary
        assert item.task.startswith(f"# Scenario {item.number}:")
        assert len(item.hints) >= 3
        assert len(item.checks) >= 4
        assert all(check.node in item.topology.nodes for check in item.checks)


@pytest.mark.parametrize("name", EXPECTED)
def test_every_scenario_has_a_runnable_reference_solution(name: str) -> None:
    item = scenario.load_scenario(name)
    assert solution_file(item.number, item.name).is_file()
    steps = solution_steps(item.number, item.name)
    assert steps, "the solution has no code blocks marked with node=..."
    for node, script in steps:
        assert node in item.topology.nodes, f"solution step for unknown node '{node}'"
        assert script.strip()


def test_unknown_scenario_lists_the_choices() -> None:
    with pytest.raises(LabnetError) as caught:
        scenario.load_scenario("nope")
    assert "static" in caught.value.hint


def write_scenario(directory: Path, scenario_yaml: str) -> Path:
    directory.mkdir()
    (directory / "scenario.yaml").write_text(scenario_yaml)
    (directory / "task.md").write_text("# Task\n")
    (directory / "topology.yaml").write_text(
        "name: custom\nnetworks: {lan: {}}\nnodes: {pc1: {role: pc, links: [lan]}}\n"
    )
    return directory


def test_scenario_from_a_directory(tmp_path: Path) -> None:
    directory = write_scenario(
        tmp_path / "mine",
        "title: Mine\nchecks:\n  - {name: has address, node: pc1, address_in: 10.0.0.0/24}\n",
    )
    item = scenario.load_scenario(str(directory))
    assert item.name == "mine"
    assert item.reference == str(directory.resolve())
    assert item.checks[0].kind == "address_in"


def test_scenario_problems_are_all_reported(tmp_path: Path) -> None:
    directory = write_scenario(
        tmp_path / "bad",
        """
title: Bad
checks:
  - {name: a, node: pc9, ping: 10.0.0.1}
  - {name: b, node: pc1, ping: 10.0.0.1, run: "true"}
  - {name: c, node: pc1, address_in: not-a-network}
  - {name: d, node: pc1, run: "true", expect: maybe}
  - {name: e, node: pc1, run: "true", colour: blue}
setup:
  pc1: setup/missing.sh
""",
    )
    with pytest.raises(ScenarioError) as caught:
        scenario.load_scenario(str(directory))
    problems = "\n".join(caught.value.problems)
    assert "check 'a': node 'pc9' is not in the topology" in problems
    assert "check 'b': needs exactly one of" in problems
    assert "check 'c': 'not-a-network' is not a network" in problems
    assert "check 'd': 'expect' must be" in problems
    assert "check 'e': unknown key 'colour'" in problems
    assert "script 'setup/missing.sh' for node 'pc1' does not exist" in problems


# --- check evaluation, with Incus replaced by a fake ----------------------


def fake_exec(monkeypatch: pytest.MonkeyPatch, returncode: int = 0, stdout: str = "") -> list:
    """Make incus.exec_in return a fixed result and record the commands it was given."""
    calls: list[list[str]] = []

    def exec_in(instance: str, command: list[str], **_kwargs: object):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    monkeypatch.setattr(scenario.incus, "exec_in", exec_in)
    return calls


def addresses_json(*addresses: str) -> str:
    info = [{"local": a.split("/")[0], "prefixlen": int(a.split("/")[1])} for a in addresses]
    return json.dumps([{"ifname": "eth0", "addr_info": info}])


def test_address_check(monkeypatch: pytest.MonkeyPatch) -> None:
    check = Check(name="addr", node="pc1", kind="address_in", target="192.168.10.0/24")

    fake_exec(monkeypatch, stdout=addresses_json("192.168.10.7/24"))
    assert scenario._evaluate(check, "x") == (True, "has 192.168.10.7/24")

    fake_exec(monkeypatch, stdout=addresses_json("10.0.0.5/8"))
    passed, detail = scenario._evaluate(check, "x")
    assert passed is False and "10.0.0.5/8" in detail

    fake_exec(monkeypatch, stdout="[]")
    assert scenario._evaluate(check, "x") == (False, "it has no IPv4 address")


def test_ping_check_can_expect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    must_work = Check(name="p", node="pc1", kind="ping", target="1.1.1.1")
    must_fail = Check(name="p", node="pc1", kind="ping", target="1.1.1.1", expect_success=False)

    calls = fake_exec(monkeypatch, returncode=0)
    assert scenario._evaluate(must_work, "x")[0] is True
    assert scenario._evaluate(must_fail, "x")[0] is False
    assert calls[0][0] == "ping" and calls[0][-1] == "1.1.1.1"

    fake_exec(monkeypatch, returncode=1)
    assert scenario._evaluate(must_work, "x")[0] is False
    assert scenario._evaluate(must_fail, "x")[0] is True


def test_command_check_conditions(monkeypatch: pytest.MonkeyPatch) -> None:
    def check(**kwargs: object) -> Check:
        return Check(name="c", node="pc1", kind="run", target="some command", **kwargs)

    fake_exec(monkeypatch, returncode=0, stdout="line one\nvalue 42\n")
    assert scenario._evaluate(check(), "x")[0] is True
    assert scenario._evaluate(check(output_contains="value 42"), "x")[0] is True
    assert scenario._evaluate(check(output_contains="value 43"), "x")[0] is False
    assert scenario._evaluate(check(output_matches=r"one.*42"), "x")[0] is True  # spans lines
    assert scenario._evaluate(check(output_matches=r"^value \d+$"), "x")[0] is False
    assert scenario._evaluate(check(exit_code=3), "x")[0] is False
    assert scenario._evaluate(check(expect_success=False), "x")[0] is False

    fake_exec(monkeypatch, returncode=3, stdout="")
    assert scenario._evaluate(check(), "x") == (False, "exit code 3: (no output)")
    assert scenario._evaluate(check(exit_code=3), "x")[0] is True
    assert scenario._evaluate(check(expect_success=False), "x")[0] is True


def test_waiting_retries_until_the_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([1, 1, 0])
    monkeypatch.setattr(
        scenario.incus,
        "exec_in",
        lambda *_a, **_k: subprocess.CompletedProcess([], next(answers), "", ""),
    )
    monkeypatch.setattr(scenario.time, "sleep", lambda _seconds: None)
    check = Check(name="p", node="pc1", kind="ping", target="10.0.0.1", wait=30)

    assert scenario._run_check(check, "x", patient=True).passed is True


def test_impatient_mode_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_exec(monkeypatch, returncode=1)
    check = Check(name="p", node="pc1", kind="ping", target="10.0.0.1", wait=30)

    assert scenario._run_check(check, "x", patient=False).passed is False
    assert len(calls) == 1
