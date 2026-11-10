"""Build the per-role container images (pc, router, server).

Lab nodes start unconfigured and often have no internet access, so they
cannot install packages when they boot. Instead labnet builds one image per
role ahead of time, with the tools for that role already installed. A node is
then just a copy of its role image, which is also much faster than installing
packages on every ``up``.

How an image is built:

1. Create a temporary network with internet access and a container from the
   base image (Debian 13 by default) attached to it.
2. Run a shell script inside it that installs packages and tidies up.
3. Stop the container and publish it as a local image called ``labnet-<role>``.
4. Delete the temporary container and network.

The recipe is the constants below plus ``_build_script``. Change it, bump
``RECIPE_VERSION``, and ``labnet images build --rebuild`` makes fresh images.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from labnet import incus
from labnet.errors import LabnetError
from labnet.plan import KEY_KIND, KEY_MANAGED, role_image_alias
from labnet.topology import ROLES

DEFAULT_BASE_IMAGE = "images:debian/13"

# Bump this whenever the recipe changes, so labnet can tell old images apart.
RECIPE_VERSION = "1"

BUILD_NETWORK = "labnet-build"

# Tools every node gets: enough to configure and debug a network by hand.
COMMON_PACKAGES = [
    "iproute2",  # ip, tc, bridge, ss
    "iputils-ping",
    "iputils-arping",
    "traceroute",
    "mtr-tiny",
    "tcpdump",
    "bind9-dnsutils",  # dig, nslookup
    "curl",
    "netcat-openbsd",
    "iperf3",
    "ethtool",
    "nftables",
    "openssh-client",
    "openssh-server",
    "nano",
    "vim-tiny",
    "less",
    "sudo",
    "bash-completion",
    "python3",  # lets Ansible manage the node
]

# Extra packages per role. They are installed but left stopped and disabled:
# configuring and starting them is the exercise.
ROLE_PACKAGES: dict[str, list[str]] = {
    "pc": [],
    "router": ["frr", "dnsmasq"],
    "server": ["dnsmasq", "kea-dhcp4-server", "bind9", "nginx"],
}

# systemd units to stop and disable after installing the packages above.
ROLE_DISABLED_UNITS: dict[str, list[str]] = {
    "pc": ["nftables"],
    "router": ["nftables", "frr", "dnsmasq"],
    "server": ["nftables", "dnsmasq", "kea-dhcp4-server", "named", "nginx"],
}

ROLE_MOTD: dict[str, str] = {
    "pc": (
        "labnet PC. It asks for an address by DHCP on every interface.\n"
        "No DHCP server on the network means no address until you set one.\n"
    ),
    "router": (
        "labnet router. IPv4 forwarding is on; nothing else is configured.\n"
        "Installed but not running: nftables, FRR (OSPF, BGP, ...), dnsmasq.\n"
    ),
    "server": (
        "labnet server. Nothing is configured.\n"
        "Installed but not running: dnsmasq, Kea DHCP, BIND 9, nginx, nftables.\n"
    ),
}

Reporter = Callable[[str], None]


def _quiet(_message: str) -> None:
    """Default reporter: say nothing."""


def image_status(role: str, images: list[dict[str, Any]] | None = None) -> str:
    """Return ``"ok"``, ``"outdated"`` or ``"missing"`` for a role's image."""
    alias = role_image_alias(role)
    for image in images if images is not None else incus.list_images():
        if any(entry.get("name") == alias for entry in image.get("aliases") or []):
            recipe = (image.get("properties") or {}).get("labnet.recipe")
            return "ok" if recipe == RECIPE_VERSION else "outdated"
    return "missing"


def ensure_role_images(roles: list[str], report: Reporter = _quiet) -> None:
    """Build the image for each role in ``roles`` that does not exist yet."""
    images = incus.list_images()
    for role in roles:
        status = image_status(role, images)
        if status == "missing":
            report(
                f"The '{role}' image does not exist yet. Building it now "
                "(first time only, this takes a few minutes)."
            )
            build_role_image(role, report=report)
        elif status == "outdated":
            report(
                f"Note: the '{role}' image was built by a different labnet version. "
                "Refresh it with: labnet images build --rebuild"
            )


def build_role_image(role: str, base: str = DEFAULT_BASE_IMAGE, report: Reporter = _quiet) -> None:
    """Build and publish the image for ``role``, replacing any existing one."""
    if role not in ROLES:
        raise LabnetError(f"Unknown role '{role}'.", hint=f"Roles are: {', '.join(ROLES)}.")
    builder = f"labnet-build-{role}"
    alias = role_image_alias(role)
    marker = {KEY_MANAGED: "true", KEY_KIND: "image-build"}

    _remove_build_leftovers(builder)
    try:
        report(f"  [{role}] creating a temporary build container from {base}")
        if not any(net["name"] == BUILD_NETWORK for net in incus.list_networks()):
            incus.create_network(
                BUILD_NETWORK,
                {**marker, "ipv4.address": "auto", "ipv4.nat": "true", "ipv6.address": "none"},
                description="labnet temporary network for building images",
            )
        pool = incus.default_storage_pool()
        incus.init_instance(
            builder,
            base,
            config={**marker, "boot.autostart": "false"},
            devices={
                "root": {"type": "disk", "path": "/", "pool": pool},
                "eth0": {"type": "nic", "network": BUILD_NETWORK, "name": "eth0"},
            },
        )
        incus.start_instances([builder])
        _wait_for_internet(builder)

        report(f"  [{role}] installing packages (the slow part)")
        result = incus.exec_in(builder, ["sh", "-s"], stdin=_build_script(role), timeout=1800)
        if result.returncode != 0:
            tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
            raise LabnetError(
                f"Building the '{role}' image failed while installing packages:\n{tail}",
                hint="Check the internet connection and that the base image is "
                "Debian or Ubuntu (the recipe uses apt), then run "
                "'labnet images build' again.",
            )

        report(f"  [{role}] saving the image as '{alias}'")
        incus.stop_instances([builder], force=False)
        if image_status(role) != "missing":
            incus.run(["image", "delete", alias])
        incus.run(
            [
                "publish",
                builder,
                "--alias",
                alias,
                "--quiet",
                f"description=labnet {role} image (built from {base})",
                f"labnet.role={role}",
                f"labnet.recipe={RECIPE_VERSION}",
                f"labnet.base={base}",
            ],
            timeout=900,
        )
        report(f"  [{role}] done")
    finally:
        _remove_build_leftovers(builder)


def remove_role_images() -> list[str]:
    """Delete all labnet role images. Returns the aliases that were removed."""
    removed = []
    for role in ROLES:
        if image_status(role) != "missing":
            incus.run(["image", "delete", role_image_alias(role)])
            removed.append(role_image_alias(role))
    return removed


def _remove_build_leftovers(builder: str) -> None:
    """Delete the build container and network if they exist and are ours."""
    for instance in incus.list_instances():
        if instance["name"] == builder:
            if instance.get("config", {}).get(KEY_MANAGED) != "true":
                raise LabnetError(
                    f"An instance called '{builder}' exists but was not created by labnet.",
                    hint="Rename or remove it, then try again.",
                )
            incus.delete_instance(builder)
    for network in incus.list_networks():
        ours = network["name"] == BUILD_NETWORK and network.get("config", {}).get(KEY_MANAGED)
        if ours and not network.get("used_by"):
            incus.delete_network(BUILD_NETWORK)


def _wait_for_internet(builder: str, timeout: float = 90) -> None:
    """Wait until the build container can resolve the Debian package mirror."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = incus.exec_in(builder, ["getent", "hosts", "deb.debian.org"], timeout=15)
        if probe.returncode == 0:
            return
        time.sleep(2)
    raise LabnetError(
        "The build container did not get internet access within "
        f"{timeout:.0f} seconds, so packages cannot be installed.",
        hint="Check that the host itself has internet access. On WSL, also see "
        "docs/troubleshooting.md.",
    )


def _build_script(role: str) -> str:
    """The shell script that turns a base container into a role image."""
    packages = " ".join(COMMON_PACKAGES + ROLE_PACKAGES[role])
    disabled = " ".join(ROLE_DISABLED_UNITS[role])
    motd = ROLE_MOTD[role]

    if role == "pc":
        network_setup = """
# PCs behave like desktops: ask for an address by DHCP on every interface.
rm -f /etc/systemd/network/*.network
cat > /etc/systemd/network/10-labnet-dhcp.network <<'EOF'
# Written by labnet. Edit or replace this file to give the PC a static address.
[Match]
Name=eth*

[Network]
DHCP=ipv4

[DHCPv4]
ClientIdentifier=mac
EOF
"""
    else:
        network_setup = """
# Routers and servers start with unconfigured interfaces. Remove the DHCP
# client configuration that the base image ships for eth0.
rm -f /etc/systemd/network/*.network
"""
    if role == "router":
        network_setup += """
# A router's job is to forward packets between its interfaces.
cat > /etc/sysctl.d/90-labnet-router.conf <<'EOF'
# Written by labnet: routers forward IPv4 packets.
net.ipv4.ip_forward = 1
EOF
"""

    return f"""set -eu
export DEBIAN_FRONTEND=noninteractive

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This base image has no apt-get; labnet role images need Debian or Ubuntu." >&2
    exit 1
fi

apt-get update -q
apt-get install -y -q --no-install-recommends {packages}

# Installed services are left for the user to configure and start.
for unit in {disabled}; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
done
{network_setup}
# Every node made from this image must get its own SSH host keys, not a copy
# of the image's. Delete them here and create new ones on first boot.
rm -f /etc/ssh/ssh_host_*
cat > /etc/systemd/system/labnet-ssh-hostkeys.service <<'EOF'
[Unit]
Description=Generate SSH host keys on first boot (labnet)
Before=ssh.service ssh.socket
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
EOF
systemctl enable labnet-ssh-hostkeys.service >/dev/null 2>&1

cat > /etc/motd <<'EOF'
{motd}EOF

# Tidy up so the image is small and nodes do not share identity.
apt-get clean
rm -rf /var/lib/apt/lists/*
# An empty machine-id makes systemd create a new one on first boot. Without
# this every node would have the same ID (and the same DHCP client identity).
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id /root/.bash_history
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
"""
