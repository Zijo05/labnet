"""Read a topology file and check it for mistakes.

A topology file is YAML that names a lab, its networks and its nodes::

    name: office
    networks:
      lan-office: {}
      wan: {uplink: true}
    nodes:
      r1:   {role: router, links: [lan-office, wan]}
      pc:   {role: pc, links: [lan-office], count: 3}    # pc01, pc02, pc03

This module knows nothing about Incus. It turns the file into plain
dataclasses (``Topology``, ``Network``, ``Node``) or raises ``TopologyError``
listing *every* problem it found, each one naming the file and the node or
network concerned.
"""

from __future__ import annotations

import difflib
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from labnet.errors import LabnetError

ROLES = ("pc", "router", "server")

# Example topologies shipped inside the package, usable by name: labnet up office
EXAMPLES_DIR = Path(__file__).parent / "examples"

# Default resource limits per role. PCs are tiny; servers get more because
# BIND, Kea and nginx together need some headroom.
ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "pc": {"memory": "256MiB", "cpu": 1},
    "router": {"memory": "384MiB", "cpu": 1},
    "server": {"memory": "512MiB", "cpu": 1},
}

# Names become parts of Linux hostnames, so keep to what a hostname allows.
_NAME_RE = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")
_MEMORY_RE = re.compile(r"^[1-9][0-9]*(MiB|GiB|MB|GB)$")

MAX_LAB_NAME = 20
MAX_NODE_NAME = 30
MAX_INSTANCE_NAME = 63  # Incus limit: an instance name is also its hostname
MAX_COUNT = 200

_TOP_KEYS = ("name", "networks", "nodes")
_NETWORK_KEYS = ("uplink", "subnet")
_NODE_KEYS = ("role", "links", "count", "memory", "cpu", "image")
_LINK_KEYS = ("network", "vlan", "trunk")


class TopologyError(LabnetError):
    """The topology file has one or more problems."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        lines = [f"{source}: {problem}" for problem in problems]
        count = "1 problem" if len(problems) == 1 else f"{len(problems)} problems"
        super().__init__(
            f"The topology file has {count}:\n  " + "\n  ".join(lines),
            hint="Fix the file and run the command again. See docs/user-guide.md "
            "for the topology file reference.",
        )


@dataclass(frozen=True)
class Network:
    """One network in the lab.

    A normal network is a bare layer-2 segment (think: an unmanaged switch).
    An ``uplink`` network is the lab's way out: it has a gateway that hands
    out addresses by DHCP and NATs to the real internet, like an ISP would.
    """

    name: str
    uplink: bool = False
    subnet: str | None = None  # only for uplink networks, e.g. "198.51.100.0/24"


@dataclass(frozen=True)
class Node:
    """One device in the lab. Its interfaces are eth0, eth1, ... in ``links`` order."""

    name: str
    role: str
    links: tuple[str, ...]
    memory: str
    cpu: int
    image: str | None = None  # None means "use the role image"
    # Optional VLAN settings of the switch port each link plugs into, keyed
    # by network name. A link has at most one of the two.
    access_vlan: dict[str, int] = field(default_factory=dict)  # untagged member of one VLAN
    trunk_vlans: dict[str, tuple[int, ...]] = field(default_factory=dict)  # tagged VLANs carried

    def interface_for(self, network: str) -> str | None:
        """Return the interface name (eth0, eth1, ...) attached to ``network``."""
        if network in self.links:
            return f"eth{self.links.index(network)}"
        return None


@dataclass(frozen=True)
class Topology:
    """A whole lab as described by the user."""

    name: str
    networks: dict[str, Network]
    nodes: dict[str, Node]
    source: str = "<topology>"


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate keys.

    Plain YAML silently keeps the last of two identical keys, so a file with
    two ``pc01:`` entries would quietly lose one node. That is a mistake worth
    reporting.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"the key '{key}' appears twice", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def builtin_examples() -> dict[str, Path]:
    """The example topologies shipped with labnet, as ``{name: file}``."""
    return {path.stem: path for path in sorted(EXAMPLES_DIR.glob("*.yaml"))}


def example_summary(path: Path) -> str:
    """The first comment line of an example file, used as its description."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") and line.strip("# "):
            return line.strip("# ")
    return ""


def find_topology(reference: str | Path) -> Path:
    """Turn what the user typed into a topology file path.

    ``reference`` is a path to a file, or the name of a built-in example such
    as ``office``. A file that exists always wins over an example of the same
    name, so ``labnet up office`` in a folder containing ``office`` (a file)
    uses that file.
    """
    path = Path(reference)
    if path.exists():
        return path
    name = str(reference).removesuffix(".yaml")
    examples = builtin_examples()
    if name in examples:
        return examples[name]
    raise LabnetError(
        f"Topology file not found: {path}",
        hint=f"Give the path of a topology file, or the name of a built-in example: "
        f"{', '.join(examples)}. 'labnet examples' lists them.",
    )


def load_topology(path: str | Path) -> Topology:
    """Read and validate a topology file, given by path or built-in example name."""
    path = find_topology(path)
    source = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LabnetError(f"Topology file not found: {path}") from None
    except OSError as exc:
        raise LabnetError(f"Cannot read {path}: {exc.strerror}") from None
    return parse_topology_text(text, source)


def parse_topology_text(text: str, source: str = "<topology>") -> Topology:
    """Parse YAML text into a validated ``Topology``."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise TopologyError(source, [f"{where}not valid YAML: {problem}"]) from None
    return parse_topology(data, source)


def parse_topology(data: Any, source: str = "<topology>") -> Topology:
    """Validate already-parsed YAML data and build a ``Topology``.

    All problems are collected and reported together, so the user can fix
    them in one pass instead of one per run.
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        raise TopologyError(
            source, ["the file must be a mapping with the keys: name, networks, nodes"]
        )
    _check_unknown_keys(data, _TOP_KEYS, "top level", problems)

    name = _parse_lab_name(data.get("name"), problems)
    networks = _parse_networks(data.get("networks"), problems)
    nodes = _parse_nodes(data.get("nodes"), networks, problems)

    if name:
        for node_name in nodes:
            instance = f"{name}-{node_name}"
            if len(instance) > MAX_INSTANCE_NAME:
                problems.append(
                    f"node '{node_name}': the container name '{instance}' is longer than "
                    f"{MAX_INSTANCE_NAME} characters; shorten the lab or node name"
                )

    if problems:
        raise TopologyError(source, problems)
    return Topology(name=name, networks=networks, nodes=nodes, source=source)


def _check_unknown_keys(
    mapping: dict, allowed: tuple[str, ...], where: str, problems: list[str]
) -> None:
    """Report keys that are not allowed, suggesting the closest valid key."""
    for key in mapping:
        if key in allowed:
            continue
        close = difflib.get_close_matches(str(key), allowed, n=1)
        suggestion = f" (did you mean '{close[0]}'?)" if close else ""
        problems.append(
            f"{where}: unknown key '{key}'{suggestion}; allowed keys: {', '.join(allowed)}"
        )


def _name_problem(name: Any, max_length: int) -> str | None:
    """Return why ``name`` is not a usable name, or None if it is fine."""
    if not isinstance(name, str):
        return f"must be text, not {type(name).__name__} (put it in quotes)"
    if len(name) > max_length:
        return f"is longer than {max_length} characters"
    if not _NAME_RE.match(name):
        return (
            "may only contain lowercase letters, digits and hyphens, "
            "and must start with a letter and not end with a hyphen"
        )
    return None


def _parse_lab_name(value: Any, problems: list[str]) -> str:
    if value is None:
        problems.append("missing 'name': give the lab a name, for example 'name: office'")
        return ""
    problem = _name_problem(value, MAX_LAB_NAME)
    if problem:
        problems.append(f"lab name '{value}' {problem}")
        return ""
    return value


def _parse_networks(value: Any, problems: list[str]) -> dict[str, Network]:
    if value is None:
        problems.append("missing 'networks': define at least one network")
        return {}
    if not isinstance(value, dict) or not value:
        problems.append("'networks' must be a mapping of network name to settings")
        return {}

    networks: dict[str, Network] = {}
    for net_name, settings in value.items():
        where = f"network '{net_name}'"
        problem = _name_problem(net_name, MAX_NODE_NAME)
        if problem:
            problems.append(f"{where}: the name {problem}")
            continue
        if settings is None:
            settings = {}  # "lan-office:" with nothing after it is fine
        if not isinstance(settings, dict):
            problems.append(f"{where}: settings must be a mapping, for example '{net_name}: {{}}'")
            continue
        _check_unknown_keys(settings, _NETWORK_KEYS, where, problems)

        uplink = settings.get("uplink", False)
        if not isinstance(uplink, bool):
            problems.append(f"{where}: 'uplink' must be true or false")
            uplink = False

        subnet = settings.get("subnet")
        if subnet is not None:
            subnet = _parse_subnet(subnet, uplink, where, problems)
        networks[net_name] = Network(name=net_name, uplink=uplink, subnet=subnet)
    return networks


def _parse_subnet(value: Any, uplink: bool, where: str, problems: list[str]) -> str | None:
    if not uplink:
        problems.append(
            f"{where}: 'subnet' is only for uplink networks. A normal network is a bare "
            "switch; giving its devices addresses is your job inside the lab"
        )
        return None
    try:
        network = ipaddress.ip_network(str(value), strict=True)
    except ValueError:
        problems.append(
            f"{where}: subnet '{value}' is not a valid network address, "
            "expected something like 198.51.100.0/24"
        )
        return None
    if network.version != 4 or not 16 <= network.prefixlen <= 29:
        problems.append(f"{where}: subnet must be IPv4 with a prefix length between /16 and /29")
        return None
    return str(network)


def _parse_nodes(value: Any, networks: dict[str, Network], problems: list[str]) -> dict[str, Node]:
    if value is None:
        problems.append("missing 'nodes': define at least one node")
        return {}
    if not isinstance(value, dict) or not value:
        problems.append("'nodes' must be a mapping of node name to settings")
        return {}

    nodes: dict[str, Node] = {}
    for entry_name, settings in value.items():
        where = f"node '{entry_name}'"
        problem = _name_problem(entry_name, MAX_NODE_NAME)
        if problem:
            problems.append(f"{where}: the name {problem}")
            continue
        if not isinstance(settings, dict):
            problems.append(
                f"{where}: settings must be a mapping, for example "
                f"'{entry_name}: {{role: pc, links: [lan]}}'"
            )
            continue
        _check_unknown_keys(settings, _NODE_KEYS, where, problems)

        role = _parse_role(settings.get("role"), where, problems)
        links, access_vlan, trunk_vlans = _parse_links(
            settings.get("links"), networks, where, problems
        )
        defaults = ROLE_DEFAULTS.get(role or "pc", ROLE_DEFAULTS["pc"])
        memory = _parse_memory(settings.get("memory", defaults["memory"]), where, problems)
        cpu = _parse_cpu(settings.get("cpu", defaults["cpu"]), where, problems)
        image = settings.get("image")
        if image is not None and (not isinstance(image, str) or not image.strip()):
            problems.append(f"{where}: 'image' must be an Incus image name like images:debian/13")
            image = None
        count = _parse_count(settings.get("count"), where, problems)
        if role is None:
            continue

        for node_name in _expand_names(entry_name, count):
            if node_name in nodes:
                problems.append(
                    f"{where}: produces the node name '{node_name}', which is already used"
                )
                continue
            nodes[node_name] = Node(
                name=node_name,
                role=role,
                links=links,
                memory=memory,
                cpu=cpu,
                image=image,
                access_vlan=access_vlan,
                trunk_vlans=trunk_vlans,
            )
    return nodes


def _parse_role(value: Any, where: str, problems: list[str]) -> str | None:
    if value is None:
        problems.append(f"{where}: missing 'role'; choose one of: {', '.join(ROLES)}")
        return None
    if value not in ROLES:
        close = difflib.get_close_matches(str(value), ROLES, n=1)
        suggestion = f" (did you mean '{close[0]}'?)" if close else ""
        problems.append(
            f"{where}: unknown role '{value}'{suggestion}; choose one of: {', '.join(ROLES)}"
        )
        return None
    return value


def _parse_links(
    value: Any, networks: dict[str, Network], where: str, problems: list[str]
) -> tuple[tuple[str, ...], dict[str, int], dict[str, tuple[int, ...]]]:
    """Parse a node's ``links`` list.

    Each item is a network name, or a mapping that also gives the VLAN
    settings of the switch port: ``{network: sw, vlan: 10}`` for an access
    port, ``{network: sw, trunk: [10, 20]}`` for a trunk port.

    Returns the network names in order, the access VLAN per network, and the
    trunk VLANs per network.
    """
    access: dict[str, int] = {}
    trunk: dict[str, tuple[int, ...]] = {}
    if value is None or value == []:
        problems.append(f"{where}: missing 'links'; connect the node to at least one network")
        return (), access, trunk
    if not isinstance(value, list):
        problems.append(f"{where}: 'links' must be a list, for example 'links: [lan-office]'")
        return (), access, trunk

    links: list[str] = []
    for item in value:
        port: dict[str, Any] = {}
        link = item
        if isinstance(item, dict):
            _check_unknown_keys(item, _LINK_KEYS, f"{where}: link", problems)
            link = item.get("network")
            port = item
            if link is None:
                problems.append(
                    f"{where}: a link written as a mapping needs 'network', "
                    "for example {network: sw, vlan: 10}"
                )
                continue
        if link in links:
            problems.append(f"{where}: is linked to network '{link}' more than once")
            continue
        if networks and link not in networks:
            close = difflib.get_close_matches(str(link), list(networks), n=1)
            suggestion = f" (did you mean '{close[0]}'?)" if close else ""
            problems.append(
                f"{where}: link '{link}' is not a defined network{suggestion}; "
                f"defined networks: {', '.join(networks)}"
            )
            continue
        links.append(link)
        _parse_port_vlans(port, link, networks, access, trunk, where, problems)
    return tuple(links), access, trunk


def _is_vlan_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4094


def _parse_port_vlans(
    port: dict[str, Any],
    link: str,
    networks: dict[str, Network],
    access: dict[str, int],
    trunk: dict[str, tuple[int, ...]],
    where: str,
    problems: list[str],
) -> None:
    """Validate the optional ``vlan`` / ``trunk`` settings of one link."""
    vlan, tagged = port.get("vlan"), port.get("trunk")
    if vlan is None and tagged is None:
        return
    here = f"{where}: link to '{link}'"
    if vlan is not None and tagged is not None:
        problems.append(f"{here}: use either 'vlan' (access port) or 'trunk', not both")
        return
    if link in networks and networks[link].uplink:
        problems.append(f"{here}: VLANs are for switches, not for the uplink network")
        return
    if vlan is not None:
        if _is_vlan_id(vlan):
            access[link] = vlan
        else:
            problems.append(f"{here}: vlan '{vlan}' is not valid; use a number from 1 to 4094")
        return
    if isinstance(tagged, list) and tagged and all(_is_vlan_id(v) for v in tagged):
        trunk[link] = tuple(sorted(set(tagged)))
    else:
        problems.append(
            f"{here}: trunk must be a list of VLAN numbers (1 to 4094), for example [10, 20]"
        )


def _parse_memory(value: Any, where: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not _MEMORY_RE.match(value):
        problems.append(
            f"{where}: memory '{value}' is not valid; write a number with a unit, "
            "for example 256MiB or 1GiB"
        )
        return ROLE_DEFAULTS["pc"]["memory"]
    return value


def _parse_cpu(value: Any, where: str, problems: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
        problems.append(f"{where}: cpu '{value}' is not valid; write a whole number such as 1 or 2")
        return 1
    return value


def _parse_count(value: Any, where: str, problems: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COUNT:
        problems.append(
            f"{where}: count '{value}' is not valid; write a whole number from 1 to {MAX_COUNT}"
        )
        return None
    return value


def _expand_names(name: str, count: int | None) -> list[str]:
    """Turn ``pc`` with ``count: 3`` into ``pc01, pc02, pc03``.

    Without ``count`` the name is used as written. Numbers are zero-padded to
    two digits (three from 100 nodes up) so that names sort naturally.
    """
    if count is None:
        return [name]
    width = max(2, len(str(count)))
    return [f"{name}{index:0{width}d}" for index in range(1, count + 1)]
