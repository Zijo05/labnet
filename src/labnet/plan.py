"""Turn a ``Topology`` into the exact Incus objects that realise it.

This is pure calculation: no Incus calls happen here, which makes the mapping
easy to unit-test and easy to explain.

    topology network  ->  one Incus bridge network   (``PlannedNetwork``)
    topology node     ->  one Incus system container (``PlannedInstance``)

Every object gets ``user.labnet.*`` config keys. Those keys, not the object
names, are how labnet later recognises what it owns.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass, field

from labnet.topology import Topology

# Config keys that mark an Incus object as created by labnet.
KEY_MANAGED = "user.labnet.managed"  # always "true"
KEY_LAB = "user.labnet.lab"  # lab name
KEY_NODE = "user.labnet.node"  # node name inside the lab (instances)
KEY_ROLE = "user.labnet.role"  # pc / router / server (instances)
KEY_LINKS = "user.labnet.links"  # comma-separated network names, in ethN order
KEY_NETWORK = "user.labnet.network"  # network name inside the lab (networks)
KEY_KIND = "user.labnet.kind"  # "segment" or "uplink" (networks)
KEY_SCENARIO = "user.labnet.scenario"  # scenario name, if the lab came from one

ROLE_IMAGE_PREFIX = "labnet-"


def instance_name(lab: str, node: str) -> str:
    """Incus instance name for a node, for example ``office-r1``.

    The name is also the hostname inside the container, so the shell prompt
    shows both the lab and the node.
    """
    return f"{lab}-{node}"


def bridge_name(lab: str, network: str) -> str:
    """Incus network (Linux bridge) name for a lab network.

    Linux limits interface names to 15 characters, which ``office-lan-office``
    already exceeds. So the bridge gets a short fixed-length name derived from
    the lab and network names: ``ln-`` plus 8 hex digits of their SHA-1 hash.
    The same input always gives the same name, so ``up`` can be run again
    safely. The human-readable names are stored in the bridge's config keys
    and description, and ``labnet list`` shows them.
    """
    digest = hashlib.sha1(f"{lab}/{network}".encode()).hexdigest()
    return f"ln-{digest[:8]}"


def role_image_alias(role: str) -> str:
    """Alias of the locally built image for a role, for example ``labnet-router``."""
    return f"{ROLE_IMAGE_PREFIX}{role}"


@dataclass
class PlannedNetwork:
    """An Incus bridge network that should exist."""

    bridge: str  # Incus name, e.g. ln-1a2b3c4d
    lab: str
    network: str  # name in the topology file
    uplink: bool
    config: dict[str, str]
    description: str


@dataclass
class PlannedInstance:
    """An Incus container that should exist."""

    name: str  # Incus name, e.g. office-r1
    lab: str
    node: str  # name in the topology file
    role: str
    image: str  # image alias or remote image to create it from
    uses_role_image: bool
    config: dict[str, str]
    devices: dict[str, dict[str, str]]


@dataclass
class Plan:
    """Everything one lab needs."""

    lab: str
    networks: list[PlannedNetwork] = field(default_factory=list)
    instances: list[PlannedInstance] = field(default_factory=list)

    def roles_needing_images(self) -> list[str]:
        """Roles for which a labnet role image must exist before building."""
        return sorted({inst.role for inst in self.instances if inst.uses_role_image})


def make_plan(topology: Topology, storage_pool: str, scenario: str | None = None) -> Plan:
    """Work out the Incus networks and instances for ``topology``.

    ``storage_pool`` is the Incus storage pool that holds the containers' root
    disks. ``scenario`` is recorded on every object when the lab belongs to a
    guided scenario.
    """
    lab = topology.name
    plan = Plan(lab=lab)

    for network in topology.networks.values():
        config = {
            KEY_MANAGED: "true",
            KEY_LAB: lab,
            KEY_NETWORK: network.name,
            KEY_KIND: "uplink" if network.uplink else "segment",
        }
        if scenario:
            config[KEY_SCENARIO] = scenario
        if network.uplink:
            # The lab's "internet": Incus acts as the ISP. It owns the gateway
            # address, hands out leases and DNS, and NATs to the outside.
            config["ipv4.address"] = _gateway_address(network.subnet)
            config["ipv4.nat"] = "true"
            config["ipv6.address"] = "none"
        else:
            # A bare switch: no address on the host side, so Incus runs no
            # DHCP, DNS or NAT on it. Whatever happens on it is up to the lab.
            config["ipv4.address"] = "none"
            config["ipv6.address"] = "none"
        plan.networks.append(
            PlannedNetwork(
                bridge=bridge_name(lab, network.name),
                lab=lab,
                network=network.name,
                uplink=network.uplink,
                config=config,
                description=f"labnet lab '{lab}', network '{network.name}'",
            )
        )

    for node in topology.nodes.values():
        config = {
            KEY_MANAGED: "true",
            KEY_LAB: lab,
            KEY_NODE: node.name,
            KEY_ROLE: node.role,
            KEY_LINKS: ",".join(node.links),
            "limits.memory": node.memory,
            "limits.cpu": str(node.cpu),
            # Lessons from the prototype: a lab must not come back by itself
            # after the host restarts and quietly eat memory.
            "boot.autostart": "false",
        }
        if scenario:
            config[KEY_SCENARIO] = scenario

        devices: dict[str, dict[str, str]] = {
            "root": {"type": "disk", "path": "/", "pool": storage_pool},
        }
        for index, link in enumerate(node.links):
            interface = f"eth{index}"
            devices[interface] = {
                "type": "nic",
                "network": bridge_name(lab, link),
                "name": interface,
            }

        plan.instances.append(
            PlannedInstance(
                name=instance_name(lab, node.name),
                lab=lab,
                node=node.name,
                role=node.role,
                image=node.image or role_image_alias(node.role),
                uses_role_image=node.image is None,
                config=config,
                devices=devices,
            )
        )
    return plan


def _gateway_address(subnet: str | None) -> str:
    """Address for the Incus side of an uplink network.

    With no subnet given, ``auto`` lets Incus pick a free private range.
    Otherwise the gateway is the first usable address of the subnet.
    """
    if subnet is None:
        return "auto"
    network = ipaddress.ip_network(subnet)
    return f"{network.network_address + 1}/{network.prefixlen}"
