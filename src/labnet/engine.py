"""The lab engine: everything labnet can do to a lab.

Both the CLI and the web UI call the functions in this module; neither talks
to Incus on its own. The functions take and return plain data (the dataclasses
below), and report progress through an optional ``report`` callback so the
caller decides whether to print it.

Ownership rule: labnet only ever touches Incus objects that carry the
``user.labnet.managed`` config key. ``read_labs`` is the single place that
decides what belongs to a lab, and every destructive function works from its
result.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from labnet import images, incus
from labnet.errors import LabnetError
from labnet.plan import (
    KEY_KIND,
    KEY_LAB,
    KEY_LINKS,
    KEY_MANAGED,
    KEY_NETWORK,
    KEY_NODE,
    KEY_ROLE,
    KEY_SCENARIO,
    Plan,
    make_plan,
)
from labnet.topology import Topology

Reporter = Callable[[str], None]

DEFAULT_SNAPSHOT = "snap"
READY_TIMEOUT = 180.0  # seconds to wait for nodes to finish booting

_ROLE_ORDER = {"router": 0, "server": 1, "pc": 2}
_DELAY_RE = re.compile(r"^[0-9]+(\.[0-9]+)?(us|ms|s)$")
_SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _quiet(_message: str) -> None:
    """Default reporter: say nothing."""


# --- what a lab looks like -------------------------------------------------


@dataclass
class InterfaceState:
    """One network interface of a node."""

    name: str  # eth0, eth1, ...
    network: str  # lab network it is plugged into
    up: bool = False
    addresses: list[str] = field(default_factory=list)  # IPv4, like 192.168.10.1/24
    shaping: str = ""  # netem settings, like "delay 50ms loss 5%"; filled on request
    vlan: str = ""  # switch port VLAN setting: "vlan 10", "trunk 10,20" or ""


@dataclass
class NodeState:
    """One node of a lab, as Incus currently reports it."""

    lab: str
    node: str
    instance: str  # Incus instance name
    role: str
    running: bool
    interfaces: list[InterfaceState] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)

    def interface_for(self, network: str) -> InterfaceState:
        """Return the interface plugged into ``network`` or explain the mistake."""
        for interface in self.interfaces:
            if interface.network == network:
                return interface
        connected = ", ".join(i.network for i in self.interfaces) or "none"
        raise LabnetError(
            f"Node '{self.node}' is not connected to a network called '{network}'.",
            hint=f"It is connected to: {connected}.",
        )


@dataclass
class NetworkState:
    """One network of a lab."""

    lab: str
    network: str
    bridge: str  # Incus network name
    uplink: bool
    gateway: str = ""  # uplink only: address of the Incus-side gateway


@dataclass
class LabState:
    """A lab: its nodes and networks."""

    name: str
    nodes: list[NodeState] = field(default_factory=list)
    networks: list[NetworkState] = field(default_factory=list)
    scenario: str | None = None

    def node(self, name: str) -> NodeState:
        """Return the node called ``name`` or explain the mistake."""
        for node in self.nodes:
            if node.node == name:
                return node
        raise LabnetError(
            f"Lab '{self.name}' has no node called '{name}'.",
            hint=f"Its nodes are: {', '.join(n.node for n in self.nodes) or 'none'}.",
        )


# --- reading ---------------------------------------------------------------


def read_labs() -> dict[str, LabState]:
    """Find every labnet lab by looking at the markers on Incus objects."""
    labs: dict[str, LabState] = {}

    for network in incus.list_networks():
        config = network.get("config") or {}
        if config.get(KEY_MANAGED) != "true" or KEY_LAB not in config:
            continue
        lab = labs.setdefault(config[KEY_LAB], LabState(name=config[KEY_LAB]))
        uplink = config.get(KEY_KIND) == "uplink"
        lab.networks.append(
            NetworkState(
                lab=lab.name,
                network=config.get(KEY_NETWORK, network["name"]),
                bridge=network["name"],
                uplink=uplink,
                gateway=config.get("ipv4.address", "") if uplink else "",
            )
        )
        lab.scenario = lab.scenario or config.get(KEY_SCENARIO)

    for instance in incus.list_instances():
        config = instance.get("config") or {}
        if config.get(KEY_MANAGED) != "true" or KEY_LAB not in config:
            continue
        lab = labs.setdefault(config[KEY_LAB], LabState(name=config[KEY_LAB]))
        lab.nodes.append(_node_state(instance, config))
        lab.scenario = lab.scenario or config.get(KEY_SCENARIO)

    for lab in labs.values():
        lab.nodes.sort(key=lambda n: (_ROLE_ORDER.get(n.role, 9), n.node))
        lab.networks.sort(key=lambda n: n.network)
    return dict(sorted(labs.items()))


def _node_state(instance: dict[str, Any], config: dict[str, str]) -> NodeState:
    """Build a ``NodeState`` from one entry of ``incus list --format json``."""
    live_interfaces = ((instance.get("state") or {}).get("network")) or {}
    interfaces = []
    links = [link for link in config.get(KEY_LINKS, "").split(",") if link]
    for index, network in enumerate(links):
        name = f"eth{index}"
        live = live_interfaces.get(name) or {}
        addresses = [
            f"{addr['address']}/{addr['netmask']}"
            for addr in live.get("addresses") or []
            if addr.get("family") == "inet" and addr.get("scope") == "global"
        ]
        device = (instance.get("devices") or {}).get(name) or {}
        vlan = ""
        if device.get("vlan"):
            vlan = f"vlan {device['vlan']}"
        elif device.get("vlan.tagged"):
            vlan = f"trunk {device['vlan.tagged']}"
        interfaces.append(
            InterfaceState(
                name=name,
                network=network,
                up=live.get("state") == "up",
                addresses=addresses,
                vlan=vlan,
            )
        )
    return NodeState(
        lab=config[KEY_LAB],
        node=config.get(KEY_NODE, instance["name"]),
        instance=instance["name"],
        role=config.get(KEY_ROLE, "pc"),
        running=instance.get("status") == "Running",
        interfaces=interfaces,
        snapshots=[snap["name"] for snap in instance.get("snapshots") or []],
    )


def get_lab(name: str) -> LabState:
    """Return one lab or explain that it does not exist."""
    labs = read_labs()
    if name not in labs:
        known = ", ".join(labs) or "none"
        raise LabnetError(
            f"There is no lab called '{name}'.",
            hint=f"Existing labs: {known}. Create one with 'labnet up <file>'.",
        )
    return labs[name]


def resolve_node(reference: str) -> NodeState:
    """Find a node from what the user typed.

    ``reference`` is either ``lab/node`` or just ``node``. The short form works
    whenever only one lab has a node of that name.
    """
    labs = read_labs()
    if "/" in reference:
        lab_name, node_name = reference.split("/", 1)
        if lab_name not in labs:
            raise LabnetError(
                f"There is no lab called '{lab_name}'.",
                hint=f"Existing labs: {', '.join(labs) or 'none'}.",
            )
        return labs[lab_name].node(node_name)

    matches = [node for lab in labs.values() for node in lab.nodes if node.node == reference]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LabnetError(
            f"No lab has a node called '{reference}'.",
            hint="Run 'labnet list' to see the nodes of every lab.",
        )
    choices = ", ".join(f"{node.lab}/{node.node}" for node in matches)
    raise LabnetError(
        f"More than one lab has a node called '{reference}'.",
        hint=f"Say which one you mean: {choices}.",
    )


def require_running(node: NodeState) -> None:
    """Raise a helpful error if ``node`` is stopped."""
    if not node.running:
        raise LabnetError(
            f"Node '{node.node}' is stopped.",
            hint=f"Start it with: labnet start {node.lab} {node.node}",
        )


# --- up / down / start / stop ----------------------------------------------


def up(
    topology: Topology,
    report: Reporter = _quiet,
    scenario: str | None = None,
    timeout: float = READY_TIMEOUT,
) -> LabState:
    """Build the lab described by ``topology`` and wait until it is ready.

    Safe to run again: objects that already exist are left alone, missing
    ones are created, stopped nodes are started. Nothing is ever changed or
    deleted by ``up``.
    """
    plan = make_plan(topology, incus.default_storage_pool(), scenario)
    existing_networks = {net["name"]: net for net in incus.list_networks()}
    existing_instances = {inst["name"]: inst for inst in incus.list_instances()}

    # Look for name clashes before creating anything, so a failed 'up' does
    # not leave half a lab behind.
    _check_name_clashes(plan, existing_networks, existing_instances)
    images.ensure_role_images(plan.roles_needing_images(), report)

    for network in plan.networks:
        if network.bridge in existing_networks:
            continue
        kind = "uplink (internet)" if network.uplink else "switch"
        report(f"  network {network.network}: creating {kind}")
        incus.create_network(network.bridge, network.config, network.description)

    for instance in plan.instances:
        existing = existing_instances.get(instance.name)
        if existing is None:
            report(f"  node {instance.node}: creating {instance.role}")
            incus.init_instance(instance.name, instance.image, instance.config, instance.devices)
        else:
            _report_drift(instance.node, existing, instance.config, report)

    to_start = [
        inst.name
        for inst in plan.instances
        if (existing_instances.get(inst.name) or {}).get("status") != "Running"
    ]
    if to_start:
        report(f"  starting {len(to_start)} node(s)")
        incus.start_instances(to_start)
    report("  waiting for every node to finish booting")
    wait_ready([inst.name for inst in plan.instances], timeout)

    lab = get_lab(topology.name)
    planned = {inst.node for inst in plan.instances}
    extra = [node.node for node in lab.nodes if node.node not in planned]
    if extra:
        report(
            f"  note: the lab also contains nodes that are not in the file: {', '.join(extra)}. "
            "They were left untouched."
        )
    return lab


def _check_name_clashes(
    plan: Plan, networks: dict[str, dict[str, Any]], instances: dict[str, dict[str, Any]]
) -> None:
    """Refuse to continue if a needed name is taken by something labnet does not own."""
    for network in plan.networks:
        config = (networks.get(network.bridge) or {}).get("config") or {}
        if network.bridge in networks and config.get(KEY_LAB) != plan.lab:
            raise LabnetError(
                f"The network name '{network.bridge}' (needed for '{network.network}') "
                "is already used by something that is not part of this lab.",
                hint="Rename the network in the topology file, or remove the other network.",
            )
    for instance in plan.instances:
        config = (instances.get(instance.name) or {}).get("config") or {}
        if instance.name in instances and config.get(KEY_LAB) != plan.lab:
            raise LabnetError(
                f"An Incus instance called '{instance.name}' already exists and is not "
                f"part of the lab '{plan.lab}'.",
                hint="Choose a different lab name or node name in the topology file. "
                "labnet never modifies instances it did not create.",
            )


def _report_drift(
    node: str, existing: dict[str, Any], wanted: dict[str, str], report: Reporter
) -> None:
    """Tell the user when an existing node no longer matches the file."""
    config = existing.get("config") or {}
    differences = [
        label
        for key, label in ((KEY_ROLE, "role"), (KEY_LINKS, "links"))
        if config.get(key) != wanted[key]
    ]
    if differences:
        report(
            f"  node {node}: already exists with different {' and '.join(differences)}; "
            "left as it is. To apply the file, remove the lab with 'labnet down' "
            "and build it again."
        )


def wait_ready(instances: list[str], timeout: float = READY_TIMEOUT) -> None:
    """Block until every instance has finished booting.

    "Ready" means systemd inside the container reports ``running`` (or
    ``degraded``: booted, but some unit failed). This is what makes ``up``
    trustworthy: when it returns, a shell or SSH login will really work.
    """
    deadline = time.monotonic() + timeout
    pending = list(instances)
    while True:
        pending = [name for name in pending if not _is_ready(name)]
        if not pending:
            return
        if time.monotonic() > deadline:
            raise LabnetError(
                f"These nodes did not finish booting within {timeout:.0f} seconds: "
                f"{', '.join(pending)}.",
                hint="Look at one with 'incus console --show-log <name>' or "
                "'incus exec <name> -- systemctl --failed'. If the host is short "
                "of memory, stop other labs first.",
            )
        time.sleep(1)


def _is_ready(instance: str) -> bool:
    """True when the instance's init system says boot is complete."""
    result = incus.exec_in(instance, ["systemctl", "is-system-running"], timeout=20)
    state = result.stdout.strip()
    if state in ("running", "degraded"):
        return True
    if result.returncode == 127:
        # No systemctl: an image without systemd (for example Alpine).
        # The best available signal is that commands can run at all.
        return incus.exec_in(instance, ["true"], timeout=20).returncode == 0
    return False


def down(lab_name: str, report: Reporter = _quiet) -> LabState:
    """Delete a lab: its nodes first, then its networks. Returns what was removed."""
    lab = get_lab(lab_name)
    for node in lab.nodes:
        report(f"  node {node.node}: deleting")
        incus.delete_instance(node.instance)
    for network in lab.networks:
        report(f"  network {network.network}: deleting")
        incus.delete_network(network.bridge)
    return lab


def start(lab_name: str, node_name: str | None = None, report: Reporter = _quiet) -> list[str]:
    """Start a whole lab or one node, and wait until ready. Returns node names started."""
    lab = get_lab(lab_name)
    nodes = [lab.node(node_name)] if node_name else lab.nodes
    stopped = [node for node in nodes if not node.running]
    if stopped:
        report(f"  starting {len(stopped)} node(s)")
        incus.start_instances([node.instance for node in stopped])
        wait_ready([node.instance for node in stopped])
    return [node.node for node in stopped]


def stop(lab_name: str, node_name: str | None = None, report: Reporter = _quiet) -> list[str]:
    """Stop a whole lab or one node. Returns node names stopped."""
    lab = get_lab(lab_name)
    nodes = [lab.node(node_name)] if node_name else lab.nodes
    running = [node for node in nodes if node.running]
    if running:
        report(f"  stopping {len(running)} node(s)")
        incus.stop_instances([node.instance for node in running])
    return [node.node for node in running]


# --- shell / exec ----------------------------------------------------------

# Start bash as a login shell where it exists, plain sh otherwise.
_LOGIN_SHELL = "command -v bash >/dev/null 2>&1 && exec bash -l; exec sh -l"


def shell_argv(node: NodeState) -> list[str]:
    """The command that opens a root console on a node.

    It goes through ``incus exec``, i.e. straight from the host into the
    container. It does not use the lab network at all, so it keeps working
    even when the node's network configuration is broken.
    """
    return [
        "incus", "exec", node.instance,
        "--env", "TERM=xterm-256color",
        "--", "sh", "-c", _LOGIN_SHELL,
    ]  # fmt: skip


def exec_argv(node: NodeState, command: list[str]) -> list[str]:
    """The command that runs ``command`` on a node, attached to the terminal."""
    return ["incus", "exec", node.instance, "--", *command]


# --- snapshots -------------------------------------------------------------


def snapshot(lab_name: str, name: str = DEFAULT_SNAPSHOT, report: Reporter = _quiet) -> list[str]:
    """Snapshot every node of a lab under ``name`` (replacing an older one)."""
    _check_snapshot_name(name)
    lab = get_lab(lab_name)
    for node in lab.nodes:
        report(f"  node {node.node}: snapshot '{name}'")
        incus.run(["snapshot", "create", node.instance, name, "--reuse"], timeout=600)
    return [node.node for node in lab.nodes]


def restore(lab_name: str, name: str = DEFAULT_SNAPSHOT, report: Reporter = _quiet) -> list[str]:
    """Put every node of a lab back to snapshot ``name``.

    A snapshot holds the node's files, not its running state. Restoring a
    running node therefore restarts it, exactly like a power cycle: settings
    saved in files come back, settings made only with commands are gone.
    """
    _check_snapshot_name(name)
    lab = get_lab(lab_name)
    missing = [node.node for node in lab.nodes if name not in node.snapshots]
    if missing:
        raise LabnetError(
            f"Snapshot '{name}' does not exist on: {', '.join(missing)}.",
            hint=f"Create it first with: labnet snapshot {lab_name} {name}",
        )
    for node in lab.nodes:
        report(f"  node {node.node}: restoring '{name}'")
        incus.run(["snapshot", "restore", node.instance, name], timeout=600)
    was_running = [node.instance for node in lab.nodes if node.running]
    if was_running:
        # Depending on the Incus version a restored container may come back
        # stopped; make sure what was running is running again.
        current = {inst["name"]: inst.get("status") for inst in incus.list_instances()}
        incus.start_instances([name_ for name_ in was_running if current.get(name_) != "Running"])
        wait_ready(was_running)
    return [node.node for node in lab.nodes]


def _check_snapshot_name(name: str) -> None:
    if not _SNAPSHOT_RE.match(name):
        raise LabnetError(
            f"'{name}' is not a usable snapshot name.",
            hint="Use letters, digits, hyphens, dots and underscores only.",
        )


# --- links -----------------------------------------------------------------


def set_link_state(node: NodeState, network: str, up: bool) -> InterfaceState:
    """Simulate pulling (``up=False``) or re-plugging a node's network cable.

    Implemented by setting the node's interface administratively down or up
    from the host side of ``incus exec``.
    """
    require_running(node)
    interface = node.interface_for(network)
    result = incus.exec_in(
        node.instance, ["ip", "link", "set", "dev", interface.name, "up" if up else "down"]
    )
    if result.returncode != 0:
        raise LabnetError(
            f"Could not change the link state of {interface.name} on '{node.node}': "
            f"{result.stderr.strip()}"
        )
    interface.up = up
    return interface


def shape_link(
    node: NodeState, network: str, delay: str | None = None, loss: float | None = None
) -> InterfaceState:
    """Add delay and/or packet loss to a link, or remove both.

    Uses the kernel's ``netem`` queueing discipline on the node's interface.
    It applies to packets the node *sends* on that link. ``delay`` is a time
    such as ``50ms``; ``loss`` is a percentage from 0 to 100. Passing neither
    (or zero for both) removes the shaping.
    """
    require_running(node)
    interface = node.interface_for(network)
    if delay is not None and not _DELAY_RE.match(delay):
        raise LabnetError(
            f"'{delay}' is not a valid delay.", hint="Write a number with a unit, like 50ms or 1s."
        )
    if loss is not None and not 0 <= loss <= 100:
        raise LabnetError(f"'{loss}' is not a valid loss percentage.", hint="Use 0 to 100.")

    has_delay = delay is not None and float(re.sub(r"[a-z]+$", "", delay)) > 0
    has_loss = loss is not None and loss > 0
    if not has_delay and not has_loss:
        # Removing a qdisc that is not there fails harmlessly; ignore it.
        incus.exec_in(node.instance, ["tc", "qdisc", "del", "dev", interface.name, "root"])
        interface.shaping = ""
        return interface

    command = ["tc", "qdisc", "replace", "dev", interface.name, "root", "netem"]
    if has_delay:
        command += ["delay", str(delay)]
    if has_loss:
        command += ["loss", f"{loss:g}%"]
    result = incus.exec_in(node.instance, command)
    if result.returncode != 0:
        raise LabnetError(
            f"Could not shape {interface.name} on '{node.node}': {result.stderr.strip()}",
            hint="The node needs the 'tc' tool (package iproute2) and the host "
            "kernel needs the sch_netem module.",
        )
    interface.shaping = _describe_shaping(delay if has_delay else None, loss if has_loss else None)
    return interface


def _describe_shaping(delay: str | None, loss: float | None) -> str:
    parts = []
    if delay:
        parts.append(f"delay {delay}")
    if loss:
        parts.append(f"loss {loss:g}%")
    return " ".join(parts)


def read_shaping(node: NodeState) -> None:
    """Fill in ``shaping`` for each interface of a running node."""
    if not node.running:
        return
    result = incus.exec_in(node.instance, ["tc", "-j", "qdisc", "show"], timeout=20)
    if result.returncode != 0:
        return
    try:
        qdiscs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return
    by_device = {q.get("dev"): q for q in qdiscs if q.get("kind") == "netem"}
    for interface in node.interfaces:
        options = (by_device.get(interface.name) or {}).get("options") or {}
        delay_seconds = (options.get("delay") or {}).get("delay") or 0
        loss_fraction = (options.get("loss-random") or {}).get("loss") or 0
        interface.shaping = _describe_shaping(
            f"{delay_seconds * 1000:g}ms" if delay_seconds else None,
            round(loss_fraction * 100, 2) if loss_fraction else None,
        )


# --- capture ---------------------------------------------------------------


def capture_argv(
    node: NodeState,
    network: str,
    count: int | None = None,
    packet_filter: str | None = None,
    raw: bool = False,
) -> list[str]:
    """The tcpdump command that captures on a node's link.

    With ``raw=False`` tcpdump prints one line per packet. With ``raw=True``
    it writes pcap data to standard output, for the caller to save to a file
    that Wireshark can open.
    """
    require_running(node)
    interface = node.interface_for(network)
    argv = ["incus", "exec", node.instance]
    if raw:
        # Binary pcap data must not pass through a terminal, which would alter it.
        argv.append("--force-noninteractive")
    argv += ["--", "tcpdump", "-i", interface.name, "-n"]
    argv += ["-U", "-w", "-"] if raw else ["-l"]
    if count:
        argv += ["-c", str(count)]
    if packet_filter:
        argv += packet_filter.split()
    return argv
