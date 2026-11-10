"""Unit tests for turning a topology into planned Incus objects. No Incus needed."""

from labnet.plan import (
    KEY_KIND,
    KEY_LAB,
    KEY_LINKS,
    KEY_MANAGED,
    KEY_SCENARIO,
    bridge_name,
    instance_name,
    make_plan,
)
from labnet.topology import parse_topology_text

OFFICE = """
name: office
networks:
  lan-office: {}
  wan: {uplink: true, subnet: 198.51.100.0/24}
  wan2: {uplink: true}
nodes:
  r1:   {role: router, links: [lan-office, wan, wan2]}
  pc01: {role: pc, links: [lan-office]}
  alp:  {role: pc, links: [lan-office], image: "images:alpine/3.20"}
"""


def office_plan(scenario: str | None = None):
    return make_plan(parse_topology_text(OFFICE), storage_pool="default", scenario=scenario)


def test_bridge_names_fit_linux_and_are_stable() -> None:
    name = bridge_name("office", "lan-office")
    assert name == bridge_name("office", "lan-office")
    assert name.startswith("ln-")
    assert len(name) <= 15
    assert name != bridge_name("office", "lan-guest")
    assert name != bridge_name("other", "lan-office")


def test_instance_name_shows_lab_and_node() -> None:
    assert instance_name("office", "r1") == "office-r1"


def test_every_object_carries_the_ownership_marker() -> None:
    plan = office_plan()
    for obj in [*plan.networks, *plan.instances]:
        assert obj.config[KEY_MANAGED] == "true"
        assert obj.config[KEY_LAB] == "office"


def test_segment_has_no_address_dhcp_or_nat() -> None:
    segment = next(n for n in office_plan().networks if n.network == "lan-office")
    assert segment.config["ipv4.address"] == "none"
    assert segment.config["ipv6.address"] == "none"
    assert "ipv4.nat" not in segment.config
    assert segment.config[KEY_KIND] == "segment"


def test_uplink_gets_gateway_and_nat() -> None:
    networks = {n.network: n for n in office_plan().networks}
    assert networks["wan"].config["ipv4.address"] == "198.51.100.1/24"
    assert networks["wan"].config["ipv4.nat"] == "true"
    assert networks["wan"].config[KEY_KIND] == "uplink"
    assert networks["wan2"].config["ipv4.address"] == "auto"


def test_router_gets_one_nic_per_link_in_order() -> None:
    router = next(i for i in office_plan().instances if i.node == "r1")
    assert router.name == "office-r1"
    assert router.config[KEY_LINKS] == "lan-office,wan,wan2"
    assert router.devices["eth0"]["network"] == bridge_name("office", "lan-office")
    assert router.devices["eth1"]["network"] == bridge_name("office", "wan")
    assert router.devices["eth2"]["name"] == "eth2"
    assert router.devices["root"] == {"type": "disk", "path": "/", "pool": "default"}


def test_limits_and_no_autostart() -> None:
    pc = next(i for i in office_plan().instances if i.node == "pc01")
    assert pc.config["limits.memory"] == "256MiB"
    assert pc.config["limits.cpu"] == "1"
    assert pc.config["boot.autostart"] == "false"


def test_role_image_versus_custom_image() -> None:
    instances = {i.node: i for i in office_plan().instances}
    assert instances["pc01"].image == "labnet-pc"
    assert instances["pc01"].uses_role_image is True
    assert instances["alp"].image == "images:alpine/3.20"
    assert instances["alp"].uses_role_image is False
    assert office_plan().roles_needing_images() == ["pc", "router"]


def test_scenario_marker_is_recorded() -> None:
    plan = office_plan(scenario="nat")
    assert all(obj.config[KEY_SCENARIO] == "nat" for obj in [*plan.networks, *plan.instances])
    assert KEY_SCENARIO not in office_plan().instances[0].config
