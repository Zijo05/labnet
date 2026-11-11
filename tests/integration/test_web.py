"""Integration tests for milestone 5: the web backend against real nodes.

These start the real server on a spare local port and talk to it over HTTP
and WebSocket, as the browser does. The page itself (JavaScript) is checked
by hand in a browser; see PROJECT_LOG.md.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

import pytest

from .conftest import on_node

pytestmark = pytest.mark.integration

websockets = pytest.importorskip("websockets")

LAB = """
networks:
  lan: {}
nodes:
  pc: {role: pc, links: [lan], count: 2}
"""


@pytest.fixture
def server() -> Iterator[str]:
    """Run 'labnet web' on a free port and return its address, like 127.0.0.1:8123."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "labnet", "web", "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    address = f"127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://{address}/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the web server did not start")
        yield address
    finally:
        process.terminate()
        process.wait(timeout=10)


def call(address: str, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    request = urllib.request.Request(
        f"http://{address}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


async def terminal_session(address: str, path: str, commands: list[str], origin: str | None) -> str:
    """Open a terminal WebSocket, type commands, and return everything printed."""
    received = b""
    async with websockets.connect(f"ws://{address}{path}", origin=origin) as ws:
        await ws.send(json.dumps({"type": "resize", "rows": 33, "cols": 111}))
        for command in commands:
            await ws.send(json.dumps({"type": "input", "data": command + "\r"}))
        try:
            while True:
                message = await asyncio.wait_for(ws.recv(), timeout=5)
                received += message if isinstance(message, bytes) else message.encode()
        except (TimeoutError, websockets.ConnectionClosed):
            pass
    return received.decode(errors="replace")


def test_browser_terminal_is_a_real_root_console(
    make_lab: Callable[[str, str], str], server: str
) -> None:
    make_lab("itweb", LAB)
    path = "/ws/terminal/itweb/pc01"

    output = asyncio.run(
        terminal_session(
            server, path, ["hostname", "id -un", "tty", "stty size", "exit"], f"http://{server}"
        )
    )
    assert "itweb-pc01" in output
    assert "root" in output
    assert "/dev/pts/" in output  # a real terminal, so nano, vtysh and friends work
    assert "33 111" in output  # the browser's size reached the node

    # Closing the browser tab must not leave shells behind on the node.
    asyncio.run(terminal_session(server, path, ["sleep 300"], f"http://{server}"))
    time.sleep(2)
    assert on_node("itweb/pc01", "pgrep -x sleep").returncode == 1

    # Another website must not be able to open a console.
    with pytest.raises(websockets.InvalidStatus):
        asyncio.run(terminal_session(server, path, ["id"], "http://evil.example"))


def test_api_drives_the_same_engine_as_the_cli(
    make_lab: Callable[[str, str], str], server: str
) -> None:
    make_lab("itweb", LAB)

    status, labs = call(server, "GET", "/api/labs?detail=true")
    assert status == 200
    lab = next(item for item in labs if item["name"] == "itweb")
    assert [node["node"] for node in lab["nodes"]] == ["pc01", "pc02"]

    link = "/api/labs/itweb/nodes/pc01/links/lan"
    assert call(server, "POST", link, {"state": "down"})[1]["up"] is False
    assert "state DOWN" in on_node("itweb/pc01", "ip link show eth0").stdout
    assert call(server, "POST", link, {"state": "up"})[1]["up"] is True

    assert call(server, "POST", link, {"delay": "30ms", "loss": "1"})[1]["shaping"] == (
        "delay 30ms loss 1%"
    )
    _, labs = call(server, "GET", "/api/labs?detail=true")
    lab = next(item for item in labs if item["name"] == "itweb")
    assert lab["nodes"][0]["interfaces"][0]["shaping"] == "delay 30ms loss 1%"
    assert call(server, "POST", link, {})[1]["shaping"] == ""

    assert call(server, "POST", "/api/labs/itweb/nodes/pc02/stop")[0] == 200
    status, error = call(server, "POST", "/api/labs/itweb/nodes/pc02/links/lan", {"state": "down"})
    assert status == 400 and "stopped" in error["error"]
    assert call(server, "POST", "/api/labs/itweb/nodes/pc02/start")[0] == 200

    assert call(server, "POST", "/api/labs/itweb/snapshot", {"name": "web"})[0] == 200
    assert call(server, "POST", "/api/labs/itweb/restore", {"name": "web"})[0] == 200

    status, error = call(server, "POST", "/api/scenario/check?ref=static")
    assert status == 400 and "has not been started" in error["error"]
