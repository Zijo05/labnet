"""Integration tests for milestone 3: snapshots, link control, capture, Ansible export."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from labnet import engine

from .conftest import labnet, on_node

pytestmark = pytest.mark.integration

TWO_PCS = """
networks:
  lan: {}
nodes:
  pc: {role: pc, links: [lan], count: 2}
"""


def give_static_addresses(lab: str) -> None:
    """Configure pc01 = 10.77.0.1 and pc02 = 10.77.0.2 with a config file each."""
    for index in (1, 2):
        script = (
            f"printf '[Match]\\nName=eth0\\n[Network]\\nAddress=10.77.0.{index}/24\\n' "
            "> /etc/systemd/network/05-static.network && networkctl reload"
        )
        assert on_node(f"{lab}/pc0{index}", script).returncode == 0
    assert ping(lab, attempts=20)


def ping(lab: str, attempts: int = 1) -> bool:
    """True if pc01 can ping pc02, trying up to ``attempts`` times."""
    for _ in range(attempts):
        if on_node(f"{lab}/pc01", "ping -c 1 -W 1 10.77.0.2").returncode == 0:
            return True
        time.sleep(1)
    return False


def average_rtt_ms(lab: str) -> float:
    output = on_node(f"{lab}/pc01", "ping -c 3 -i 0.3 -W 2 10.77.0.2").stdout
    match = re.search(r"= [\d.]+/([\d.]+)/", output)
    assert match, output
    return float(match.group(1))


def test_snapshot_and_restore(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itops", TWO_PCS)
    assert on_node("itops/pc01", "echo original > /root/note").returncode == 0

    saved = labnet("snapshot", "itops", "before")
    assert saved.returncode == 0, saved.stderr
    assert engine.get_lab("itops").node("pc01").snapshots == ["before"]

    assert on_node("itops/pc01", "echo changed > /root/note; touch /root/extra").returncode == 0

    # Restoring asks first; without a terminal it must be confirmed with --yes.
    assert labnet("restore", "itops", "before").returncode == 1
    restored = labnet("restore", "itops", "before", "--yes")
    assert restored.returncode == 0, restored.stderr

    # Files are back as they were, and the nodes are running and ready again.
    assert on_node("itops/pc01", "cat /root/note").stdout.strip() == "original"
    assert on_node("itops/pc01", "test -e /root/extra").returncode == 1
    assert all(node.running for node in engine.get_lab("itops").nodes)
    assert on_node("itops/pc02", "systemctl is-system-running").stdout.strip() == "running"

    # Asking for a snapshot that does not exist explains what to do.
    missing = labnet("restore", "itops", "nope", "--yes")
    assert missing.returncode == 1
    assert "labnet snapshot itops nope" in missing.stderr


def test_link_down_and_up(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itops", TWO_PCS)
    give_static_addresses("itops")

    assert labnet("link", "down", "itops/pc02", "lan").returncode == 0
    assert not ping("itops")
    assert "DOWN" in labnet("list", "itops").stdout
    assert engine.get_lab("itops").node("pc02").interfaces[0].up is False

    assert labnet("link", "up", "itops/pc02", "lan").returncode == 0
    assert ping("itops", attempts=20)

    wrong = labnet("link", "down", "itops/pc02", "wan")
    assert wrong.returncode == 1
    assert "It is connected to: lan" in wrong.stderr


def test_link_delay_and_loss(make_lab: Callable[[str, str], str]) -> None:
    make_lab("itops", TWO_PCS)
    give_static_addresses("itops")
    assert average_rtt_ms("itops") < 20

    shaped = labnet("link", "set", "itops/pc01", "lan", "--delay", "100ms")
    assert shaped.returncode == 0, shaped.stderr
    assert average_rtt_ms("itops") >= 90
    assert "delay 100ms" in labnet("list", "itops").stdout

    # 100% loss: nothing pc01 sends arrives.
    assert labnet("link", "set", "itops/pc01", "lan", "--loss", "100").returncode == 0
    assert not ping("itops")
    assert "loss 100%" in labnet("list", "itops").stdout

    assert labnet("link", "clear", "itops/pc01", "lan").returncode == 0
    assert ping("itops", attempts=5)
    assert average_rtt_ms("itops") < 20

    assert labnet("link", "set", "itops/pc01", "lan", "--delay", "fast").returncode == 1


def test_capture_to_screen_and_to_file(make_lab: Callable[[str, str], str], tmp_path: Path) -> None:
    make_lab("itops", TWO_PCS)
    give_static_addresses("itops")

    def capture_while_pinging(*extra: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "labnet", "capture", "itops/pc02", "lan", "-c", "2"]
        capture = subprocess.Popen(
            [*command, *extra, "icmp"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)  # let tcpdump start listening
        on_node("itops/pc01", "ping -c 3 -W 1 10.77.0.2")
        stdout, stderr = capture.communicate(timeout=30)
        return subprocess.CompletedProcess(command, capture.returncode, stdout, stderr)

    shown = capture_while_pinging()
    assert shown.returncode == 0, shown.stderr
    assert "ICMP echo request" in shown.stdout
    assert "10.77.0.1 > 10.77.0.2" in shown.stdout

    pcap = tmp_path / "ping.pcap"
    saved = capture_while_pinging("-w", str(pcap))
    assert saved.returncode == 0, saved.stderr
    data = pcap.read_bytes()
    # A pcap file starts with a magic number (little-endian a1b2c3d4).
    assert data[:4] == bytes.fromhex("d4c3b2a1")
    assert len(data) > 100


@pytest.mark.skipif(shutil.which("ansible") is None, reason="Ansible is not installed")
def test_ansible_inventory_reaches_unconfigured_nodes(
    make_lab: Callable[[str, str], str], tmp_path: Path
) -> None:
    make_lab("itops", TWO_PCS)
    inventory = tmp_path / "inventory.ini"

    exported = labnet("export", "ansible", "itops", "-o", str(inventory))
    assert exported.returncode == 0, exported.stderr
    assert "pc01 ansible_host=itops-pc01" in inventory.read_text()

    # The nodes have no addresses at all, yet Ansible reaches them via Incus.
    result = subprocess.run(
        ["ansible", "-i", str(inventory), "all", "-m", "ping", "-o"],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("SUCCESS") == 2
