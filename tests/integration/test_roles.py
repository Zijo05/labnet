"""Integration tests for milestone 2: roles, several segments and the uplink.

The lab is configured the way a learner would do it, with configuration
files, and then checked: routing between two LANs, DHCP from a server, and
NAT to the real internet. The NAT test needs the host to be online.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from labnet import engine, images

from .conftest import labnet, on_node

pytestmark = pytest.mark.integration

OFFICE = """
networks:
  lan-a: {}
  lan-b: {}
  wan: {uplink: true}
nodes:
  r1:   {role: router, links: [lan-a, lan-b, wan]}
  srv1: {role: server, links: [lan-a]}
  pc01: {role: pc, links: [lan-a]}
  pc02: {role: pc, links: [lan-b]}
"""

ROUTER_SETUP = r"""
cd /etc/systemd/network
printf '[Match]\nName=eth0\n[Network]\nAddress=192.168.10.1/24\n' > 10-a.network
printf '[Match]\nName=eth1\n[Network]\nAddress=192.168.20.1/24\n' > 20-b.network
printf '[Match]\nName=eth2\n[Network]\nDHCP=ipv4\n' > 30-wan.network
networkctl reload
cat > /etc/nftables.conf <<'EOF'
flush ruleset
table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "eth2" masquerade
    }
}
EOF
systemctl enable --now nftables
"""

SERVER_SETUP = r"""
printf '[Match]\nName=eth0\n[Network]\nAddress=192.168.10.2/24\nGateway=192.168.10.1\n' \
    > /etc/systemd/network/10-lan.network
networkctl reload
cat > /etc/dnsmasq.d/lab.conf <<'EOF'
interface=eth0
bind-interfaces
port=0
dhcp-range=192.168.10.100,192.168.10.150,1h
dhcp-option=option:router,192.168.10.1
EOF
systemctl enable --now dnsmasq
"""

PC_B_SETUP = r"""
printf '[Match]\nName=eth0\n[Network]\nAddress=192.168.20.50/24\nGateway=192.168.20.1\n' \
    > /etc/systemd/network/05-static.network
networkctl reload
"""


def run_script(node: str, script: str) -> None:
    result = labnet("exec", node, "--", "sh", "-es", stdin=script)
    assert result.returncode == 0, f"{node}: {result.stdout}{result.stderr}"


def wait_for(node: str, command: str, attempts: int = 45) -> bool:
    """Retry a command on a node about once a second until it succeeds."""
    retry = f"i=0; until {command}; do i=$((i+1)); [ $i -ge {attempts} ] && exit 1; sleep 1; done"
    return on_node(node, retry).returncode == 0


def test_role_images_are_built_from_the_recipe() -> None:
    images.ensure_role_images(["pc", "router", "server"])
    for role in ("pc", "router", "server"):
        assert images.image_status(role) == "ok"
        image = images.find_role_image(role)
        assert image is not None
        assert image["properties"]["labnet.role"] == role
        assert image["properties"]["labnet.recipe"] == images.RECIPE_VERSION


def test_roles_start_in_the_documented_state(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itroles", OFFICE)

    # Only the router forwards packets.
    assert on_node("itroles/r1", "sysctl -n net.ipv4.ip_forward").stdout.strip() == "1"
    assert on_node("itroles/srv1", "sysctl -n net.ipv4.ip_forward").stdout.strip() == "0"
    assert on_node("itroles/pc01", "sysctl -n net.ipv4.ip_forward").stdout.strip() == "0"

    # Tools are installed; services are present but neither enabled nor running.
    assert on_node("itroles/r1", "command -v vtysh && command -v nft").returncode == 0
    for unit in ("frr", "dnsmasq", "nftables"):
        assert on_node("itroles/r1", f"systemctl is-active {unit}").stdout.strip() == "inactive"
    for unit in ("dnsmasq", "kea-dhcp4-server", "named", "nginx"):
        assert on_node("itroles/srv1", f"systemctl is-enabled {unit}").stdout.strip() == "disabled"
    for tool in ("tcpdump", "dig", "traceroute", "nano", "python3"):
        assert on_node("itroles/pc01", f"command -v {tool}").returncode == 0, tool

    # Nothing has an IPv4 address yet, not even the router's WAN side.
    lab = engine.get_lab("itroles")
    assert all(not iface.addresses for node in lab.nodes for iface in node.interfaces)
    uplink = next(net for net in lab.networks if net.uplink)
    assert uplink.network == "wan" and uplink.gateway.endswith("/24")


def test_hand_configured_lab_routes_serves_dhcp_and_nats(
    make_lab: Callable[[str, str], str],
) -> None:
    make_lab("itroles", OFFICE)
    run_script("itroles/r1", ROUTER_SETUP)
    run_script("itroles/srv1", SERVER_SETUP)
    run_script("itroles/pc02", PC_B_SETUP)

    # The router got its WAN address from the uplink's DHCP ("the ISP").
    assert wait_for("itroles/r1", "ip -4 -o addr show eth2 | grep -q 'inet '")

    # Routing: a static host on LAN B reaches the server on LAN A through r1.
    assert wait_for("itroles/pc02", "ping -c 1 -W 1 192.168.10.2 >/dev/null")

    # DHCP: pc01 is a plain DHCP client and gets a lease from srv1's dnsmasq.
    assert wait_for("itroles/pc01", "ip -4 -o addr show eth0 | grep -q 'inet 192.168.10.1'", 90)
    assert "default via 192.168.10.1" in on_node("itroles/pc01", "ip route").stdout

    # NAT: both LANs reach the real internet through r1's masquerade rule.
    assert wait_for("itroles/pc01", "ping -c 1 -W 2 1.1.1.1 >/dev/null", 10)
    assert wait_for("itroles/pc02", "ping -c 1 -W 2 1.1.1.1 >/dev/null", 10)

    # The configuration lives in files, so it survives a restart of the lab.
    assert labnet("stop", "itroles").returncode == 0
    assert labnet("start", "itroles").returncode == 0
    assert wait_for("itroles/pc02", "ping -c 1 -W 1 192.168.10.2 >/dev/null")
    assert wait_for("itroles/pc02", "ping -c 1 -W 2 1.1.1.1 >/dev/null", 30)
