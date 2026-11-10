"""Unit tests for reading and validating topology files. No Incus needed."""

from pathlib import Path

import pytest

from labnet.errors import LabnetError
from labnet.topology import TopologyError, load_topology, parse_topology_text

OFFICE = """
name: office
networks:
  lan-office: {}
  lan-guest:
  wan: {uplink: true, subnet: 198.51.100.0/24}
nodes:
  r1:    {role: router, links: [lan-office, lan-guest, wan]}
  dhcp1: {role: server, links: [lan-office], memory: 1GiB, cpu: 2}
  pc:    {role: pc, links: [lan-office], count: 3}
"""


def problems_of(text: str) -> list[str]:
    """Parse text that must be invalid and return the list of problems."""
    with pytest.raises(TopologyError) as caught:
        parse_topology_text(text, "lab.yaml")
    return caught.value.problems


def test_valid_topology_is_parsed() -> None:
    topology = parse_topology_text(OFFICE, "office.yaml")
    assert topology.name == "office"
    assert list(topology.networks) == ["lan-office", "lan-guest", "wan"]
    assert topology.networks["wan"].uplink is True
    assert topology.networks["wan"].subnet == "198.51.100.0/24"
    assert topology.networks["lan-guest"].uplink is False
    assert list(topology.nodes) == ["r1", "dhcp1", "pc01", "pc02", "pc03"]


def test_role_defaults_and_overrides() -> None:
    topology = parse_topology_text(OFFICE)
    assert topology.nodes["pc01"].memory == "256MiB"
    assert topology.nodes["pc01"].cpu == 1
    assert topology.nodes["r1"].memory == "384MiB"
    assert topology.nodes["dhcp1"].memory == "1GiB"
    assert topology.nodes["dhcp1"].cpu == 2


def test_interfaces_follow_link_order() -> None:
    router = parse_topology_text(OFFICE).nodes["r1"]
    assert router.interface_for("lan-office") == "eth0"
    assert router.interface_for("wan") == "eth2"
    assert router.interface_for("nowhere") is None


def test_count_pads_names_so_they_sort() -> None:
    text = "name: big\nnetworks: {lan: {}}\nnodes:\n  pc: {role: pc, links: [lan], count: 120}\n"
    names = list(parse_topology_text(text).nodes)
    assert names[0] == "pc001"
    assert names[-1] == "pc120"
    assert names == sorted(names)


def test_error_names_the_file_and_the_node() -> None:
    text = "name: lab\nnetworks: {lan: {}}\nnodes:\n  r1: {role: router, links: [lam]}\n"
    with pytest.raises(TopologyError) as caught:
        parse_topology_text(text, "lab.yaml")
    message = str(caught.value)
    assert "lab.yaml" in message
    assert "node 'r1'" in message
    assert "'lam' is not a defined network" in message
    assert "did you mean 'lan'" in message


def test_all_problems_are_reported_together() -> None:
    text = """
name: Bad_Name
networks: {lan: {}}
nodes:
  pc1: {role: desktop, links: [lan]}
  pc2: {role: pc}
  pc3: {role: pc, links: [lan], memory: lots}
"""
    problems = problems_of(text)
    assert len(problems) == 4
    assert any("lab name 'Bad_Name'" in p for p in problems)
    assert any("unknown role 'desktop'" in p for p in problems)
    assert any("node 'pc2': missing 'links'" in p for p in problems)
    assert any("memory 'lots'" in p for p in problems)


def test_unknown_key_suggests_the_right_one() -> None:
    text = "name: lab\nnetworks: {lan: {}}\nnodes:\n  pc1: {role: pc, link: [lan]}\n"
    problems = problems_of(text)
    assert any("unknown key 'link' (did you mean 'links'?)" in p for p in problems)


def test_duplicate_node_is_rejected() -> None:
    text = (
        "name: lab\nnetworks: {lan: {}}\nnodes:\n"
        "  pc1: {role: pc, links: [lan]}\n  pc1: {role: pc, links: [lan]}\n"
    )
    problems = problems_of(text)
    assert "the key 'pc1' appears twice" in problems[0]
    assert "line 5" in problems[0]


def test_count_colliding_with_explicit_name() -> None:
    text = (
        "name: lab\nnetworks: {lan: {}}\nnodes:\n"
        "  pc01: {role: pc, links: [lan]}\n  pc: {role: pc, links: [lan], count: 2}\n"
    )
    assert any("'pc01', which is already used" in p for p in problems_of(text))


def test_subnet_only_on_uplink() -> None:
    text = (
        "name: lab\nnetworks: {lan: {subnet: 10.0.0.0/24}}\n"
        "nodes: {pc1: {role: pc, links: [lan]}}\n"
    )
    assert any("'subnet' is only for uplink networks" in p for p in problems_of(text))


def test_bad_subnet() -> None:
    text = (
        "name: lab\nnetworks: {wan: {uplink: true, subnet: 10.0.0.5/24}}\n"
        "nodes: {r1: {role: router, links: [wan]}}\n"
    )
    assert any("not a valid network address" in p for p in problems_of(text))


def test_same_network_linked_twice() -> None:
    text = "name: lab\nnetworks: {lan: {}}\nnodes: {r1: {role: router, links: [lan, lan]}}\n"
    assert any("more than once" in p for p in problems_of(text))


def test_numeric_name_must_be_quoted() -> None:
    text = "name: lab\nnetworks: {lan: {}}\nnodes: {1: {role: pc, links: [lan]}}\n"
    assert any("must be text" in p for p in problems_of(text))


def test_too_long_instance_name() -> None:
    node = "n" + "x" * 29
    lab = "l" + "a" * 19
    text = f"name: {lab}\nnetworks: {{lan: {{}}}}\nnodes: {{{node}: {{role: pc, links: [lan]}}}}\n"
    assert parse_topology_text(text).nodes  # 20 + 1 + 30 = 51 characters: fine


def test_invalid_yaml_reports_a_line() -> None:
    problems = problems_of("name: lab\nnetworks: {lan: {}\nnodes: [")
    assert "not valid YAML" in problems[0]


def test_empty_file() -> None:
    assert "must be a mapping" in problems_of("")[0]


def test_missing_sections() -> None:
    problems = problems_of("name: lab\n")
    assert any("missing 'networks'" in p for p in problems)
    assert any("missing 'nodes'" in p for p in problems)


def test_missing_file_gives_a_hint(tmp_path: Path) -> None:
    with pytest.raises(LabnetError) as caught:
        load_topology(tmp_path / "nope.yaml")
    assert "not found" in caught.value.message
    assert caught.value.hint


def test_shipped_examples_are_valid() -> None:
    examples = Path(__file__).resolve().parents[2] / "examples"
    files = sorted(examples.glob("*.yaml"))
    assert files, "no example topologies found"
    for file in files:
        assert load_topology(file).nodes
