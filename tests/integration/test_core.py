"""Integration tests for milestone 1: up, down, list, exec, start, stop."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from labnet import engine, incus

from .conftest import labnet, on_node, remove_lab

pytestmark = pytest.mark.integration

TWO_PCS = """
networks:
  lan: {}
nodes:
  pc: {role: pc, links: [lan], count: 2}
"""


def test_up_builds_a_ready_lab_and_is_idempotent(make_lab: Callable[[str, str], str]) -> None:
    lab_file = make_lab("itcore", TWO_PCS)

    lab = engine.get_lab("itcore")
    assert [node.node for node in lab.nodes] == ["pc01", "pc02"]
    assert all(node.running for node in lab.nodes)
    assert [net.network for net in lab.networks] == ["lan"]

    # "Ready" must mean ready: systemd finished booting and sshd is up.
    assert on_node("itcore/pc01", "systemctl is-system-running").stdout.strip() == "running"
    assert on_node("itcore/pc01", "systemctl is-active ssh").stdout.strip() == "active"

    # Running 'up' again must not recreate anything.
    created_before = {i["name"]: i["created_at"] for i in incus.list_instances()}
    again = labnet("up", lab_file)
    assert again.returncode == 0, again.stderr
    assert "creating" not in again.stdout
    created_after = {i["name"]: i["created_at"] for i in incus.list_instances()}
    assert created_before == created_after


def test_nodes_start_unconfigured_and_share_a_switch(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itcore", TWO_PCS)

    # No DHCP server on a plain switch, so a PC has no IPv4 address ...
    assert engine.get_lab("itcore").node("pc01").interfaces[0].addresses == []
    # ... and autostart is off, so a host restart does not bring the lab back.
    config = next(i for i in incus.list_instances() if i["name"] == "itcore-pc01")["config"]
    assert config["boot.autostart"] == "false"

    # Each node has its own identity (not a copy of the image's).
    ids = {on_node(f"itcore/{n}", "cat /etc/machine-id").stdout.strip() for n in ("pc01", "pc02")}
    assert len(ids) == 2 and "" not in ids

    # Static addresses by hand: the two PCs reach each other over the switch.
    assert on_node("itcore/pc01", "ip addr add 10.99.0.1/24 dev eth0").returncode == 0
    assert on_node("itcore/pc02", "ip addr add 10.99.0.2/24 dev eth0").returncode == 0
    assert on_node("itcore/pc01", "ping -c 2 -W 2 10.99.0.2").returncode == 0

    listing = labnet("list", "itcore")
    assert "10.99.0.1/24" in listing.stdout
    assert "lan (switch)" in listing.stdout


def test_exec_passes_exit_codes_and_stop_start_work(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itcore", TWO_PCS)

    assert on_node("itcore/pc01", "exit 7").returncode == 7
    # The short node name works while only one lab has a node called pc02.
    assert labnet("exec", "itcore/pc02", "--", "hostname").stdout.strip() == "itcore-pc02"

    assert labnet("stop", "itcore", "pc02").returncode == 0
    assert engine.get_lab("itcore").node("pc02").running is False
    refused = labnet("exec", "itcore/pc02", "--", "true")
    assert refused.returncode == 1
    assert "labnet start itcore pc02" in refused.stderr

    assert labnet("start", "itcore").returncode == 0
    assert all(node.running for node in engine.get_lab("itcore").nodes)


def test_down_needs_confirmation_and_removes_only_the_lab(
    make_lab: Callable[[str, str], str],
) -> None:
    make_lab("itcore", TWO_PCS)
    before_instances = {i["name"] for i in incus.list_instances()}
    before_networks = {n["name"] for n in incus.list_networks()}

    unconfirmed = labnet("down", "itcore")
    assert unconfirmed.returncode == 1
    assert "--yes" in unconfirmed.stderr
    assert "itcore" in engine.read_labs()

    assert labnet("down", "itcore", "--yes").returncode == 0
    assert "itcore" not in engine.read_labs()

    # Exactly the lab's own objects are gone; everything else is untouched.
    removed_instances = before_instances - {i["name"] for i in incus.list_instances()}
    removed_networks = before_networks - {n["name"] for n in incus.list_networks()}
    assert removed_instances == {"itcore-pc01", "itcore-pc02"}
    assert len(removed_networks) == 1 and all(n.startswith("ln-") for n in removed_networks)


def test_up_refuses_to_take_over_a_foreign_instance(tmp_path) -> None:
    """A container labnet did not create must never be adopted or modified."""
    remove_lab("itclash")
    foreign = "itclash-pc01"
    subprocess.run(["incus", "delete", "--force", foreign], capture_output=True, check=False)
    pool = incus.default_storage_pool()
    subprocess.run(
        ["incus", "init", "labnet-pc", foreign, "--no-profiles", "--storage", pool, "--quiet"],
        check=True,
        capture_output=True,
    )
    try:
        path = tmp_path / "itclash.yaml"
        path.write_text(
            "name: itclash\nnetworks: {lan: {}}\nnodes: {pc01: {role: pc, links: [lan]}}\n"
        )
        result = labnet("up", str(path))
        assert result.returncode == 1
        assert "already exists and is not part of the lab" in result.stderr
        # Nothing was created, and the foreign container is still there, unmarked.
        assert "itclash" not in engine.read_labs()
        survivor = next(i for i in incus.list_instances() if i["name"] == foreign)
        assert "user.labnet.managed" not in survivor["config"]
    finally:
        subprocess.run(["incus", "delete", "--force", foreign], capture_output=True, check=False)
        remove_lab("itclash")
