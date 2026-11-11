"""Integration test for the VLAN stretch goal: access ports, a trunk, router on a stick."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from labnet import engine

from .conftest import labnet, on_node

pytestmark = pytest.mark.integration

VLAN_LAB = """
networks:
  sw: {}
nodes:
  r1:  {role: router, links: [{network: sw, trunk: [10, 20]}]}
  pc1: {role: pc, links: [{network: sw, vlan: 10}]}
  pc2: {role: pc, links: [{network: sw, vlan: 20}]}
  pc3: {role: pc, links: [{network: sw, vlan: 10}]}
"""


def test_switch_separates_vlans_and_router_on_a_stick_joins_them(
    make_lab: Callable[[str, str], str],
) -> None:
    make_lab("itvlan", VLAN_LAB)

    lab = engine.get_lab("itvlan")
    assert lab.node("pc1").interfaces[0].vlan == "vlan 10"
    assert lab.node("r1").interfaces[0].vlan == "trunk 10,20"
    assert "sw (trunk 10,20)" in labnet("list", "itvlan").stdout

    # All three PCs in one IP subnet: only the switch's VLANs can separate them.
    for index in (1, 2, 3):
        assert (
            on_node(f"itvlan/pc{index}", f"ip addr add 10.5.0.{index}/24 dev eth0").returncode == 0
        )
    assert on_node("itvlan/pc1", "ping -c 2 -W 1 10.5.0.3").returncode == 0  # VLAN 10 to VLAN 10
    assert on_node("itvlan/pc1", "ping -c 2 -W 1 10.5.0.2").returncode != 0  # VLAN 10 to VLAN 20

    # Router on a stick: one tagged sub-interface per VLAN on the trunk.
    router = """
ip link add link eth0 name eth0.10 type vlan id 10
ip link add link eth0 name eth0.20 type vlan id 20
ip link set eth0.10 up
ip link set eth0.20 up
ip addr add 192.168.10.1/24 dev eth0.10
ip addr add 192.168.20.1/24 dev eth0.20
"""
    assert labnet("exec", "itvlan/r1", "--", "sh", "-es", stdin=router).returncode == 0
    for node, address, gateway in (
        ("pc1", "192.168.10.11", "192.168.10.1"),
        ("pc2", "192.168.20.12", "192.168.20.1"),
    ):
        setup = (
            f"ip addr flush dev eth0; ip addr add {address}/24 dev eth0; "
            f"ip route add default via {gateway}"
        )
        assert on_node(f"itvlan/{node}", setup).returncode == 0

    # Different VLANs and subnets now reach each other, through r1.
    assert on_node("itvlan/pc1", "ping -c 2 -W 1 192.168.20.12").returncode == 0
    trace = on_node("itvlan/pc1", "traceroute -n -w 1 -q 1 192.168.20.12").stdout
    assert "192.168.10.1" in trace
