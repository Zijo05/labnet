"""Unit tests for the Ansible inventory text. No Incus needed."""

from labnet.ansible_export import inventory_text
from labnet.engine import LabState, NodeState


def node(name: str, role: str) -> NodeState:
    return NodeState(
        lab="my-office", node=name, instance=f"my-office-{name}", role=role, running=True
    )


def test_inventory_groups_nodes_by_role_and_uses_incus_connection() -> None:
    lab = LabState(name="my-office", nodes=[node("r1", "router"), node("pc01", "pc")])
    text = inventory_text(lab)
    lines = text.splitlines()

    assert "[routers]" in lines
    assert "r1 ansible_host=my-office-r1" in lines
    assert "[pcs]" in lines
    assert "pc01 ansible_host=my-office-pc01" in lines
    # No servers in this lab, so no empty group.
    assert "[servers]" not in lines
    # Hyphens are not allowed in Ansible group names.
    assert "[lab_my_office:children]" in lines
    assert "ansible_connection=community.general.incus" in lines
    assert lines.index("[lab_my_office:children]") < lines.index("[lab_my_office:vars]")
