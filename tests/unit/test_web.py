"""Unit tests for the web backend, with the engine replaced by fakes. No Incus needed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from labnet import engine  # noqa: E402
from labnet.engine import InterfaceState, LabState, NetworkState, NodeState  # noqa: E402
from labnet.errors import LabnetError  # noqa: E402
from labnet.web import app as web  # noqa: E402

LOCAL = web.LOCAL_HOSTS


def sample_lab() -> LabState:
    router = NodeState(
        lab="office",
        node="r1",
        instance="office-r1",
        role="router",
        running=True,
        interfaces=[InterfaceState(name="eth0", network="lan", up=True, addresses=["10.0.0.1/24"])],
    )
    return LabState(
        name="office",
        nodes=[router],
        networks=[NetworkState(lab="office", network="lan", bridge="ln-1", uplink=False)],
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(engine, "read_labs", lambda: {"office": sample_lab()})
    monkeypatch.setattr(engine, "get_lab", lambda name: sample_lab())
    # The test client's default Host header is "testserver"; use a real local one.
    return TestClient(web.create_app(), base_url="http://127.0.0.1:8080")


# --- the trust check ---------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "origin", "trusted"),
    [
        ("127.0.0.1:8080", None, True),  # curl, or a typed-in address
        ("localhost:8080", "http://localhost:8080", True),  # our own page
        ("[::1]:8080", "http://[::1]:8080", True),
        ("localhost", "http://localhost", True),
        ("127.0.0.1:8080", "http://evil.example", False),  # another site calling us
        ("127.0.0.1:8080", "http://localhost:8080", False),  # origin and host disagree
        ("evil.example:8080", "http://evil.example:8080", False),  # DNS rebinding
        ("evil.example:8080", None, False),
        ("192.168.1.5:8080", "http://192.168.1.5:8080", False),  # not allowed unless asked for
        (None, None, False),
    ],
)
def test_is_trusted_request(host: str | None, origin: str | None, trusted: bool) -> None:
    assert web.is_trusted_request(host, origin, LOCAL) is trusted


def test_extra_host_is_trusted_only_when_configured() -> None:
    allowed = LOCAL | {"192.168.1.5"}
    assert web.is_trusted_request("192.168.1.5:8080", "http://192.168.1.5:8080", allowed)


def test_cross_site_requests_are_refused(client: TestClient) -> None:
    assert client.get("/api/labs").status_code == 200
    assert client.get("/api/labs", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/labs", headers={"Host": "evil.example:8080"}).status_code == 403
    refused = client.post("/api/labs/office/stop", headers={"Origin": "http://evil.example"})
    assert refused.status_code == 403


def test_cross_site_terminal_is_refused(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/ws/terminal/office/r1", headers={"Origin": "http://evil.example"}
        ),
    ):
        pass


# --- the API -----------------------------------------------------------------


def test_labs_are_returned_as_json(client: TestClient) -> None:
    labs = client.get("/api/labs").json()
    assert labs[0]["name"] == "office"
    node = labs[0]["nodes"][0]
    assert node["node"] == "r1" and node["running"] is True
    assert node["interfaces"][0] == {
        "name": "eth0",
        "network": "lan",
        "up": True,
        "addresses": ["10.0.0.1/24"],
        "shaping": "",
    }
    assert labs[0]["networks"][0]["uplink"] is False


def test_engine_errors_become_readable_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_lab: str) -> list[str]:
        raise LabnetError("There is no lab called 'nope'.", hint="Create one first.")

    monkeypatch.setattr(engine, "stop", fail)
    response = client.post("/api/labs/nope/stop")
    assert response.status_code == 400
    assert response.json() == {
        "error": "There is no lab called 'nope'.",
        "hint": "Create one first.",
    }


def test_lab_and_link_actions_call_the_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        engine, "snapshot", lambda lab, name: calls.append(("snap", lab, name)) or []
    )

    def set_state(node: NodeState, network: str, up: bool) -> InterfaceState:
        calls.append(("state", node.node, network, up))
        return InterfaceState(name="eth0", network=network, up=up)

    def shape(node: NodeState, network: str, delay=None, loss=None) -> InterfaceState:
        calls.append(("shape", node.node, network, delay, loss))
        return InterfaceState(name="eth0", network=network, up=True, shaping="delay 50ms")

    monkeypatch.setattr(engine, "set_link_state", set_state)
    monkeypatch.setattr(engine, "shape_link", shape)

    assert client.post("/api/labs/office/snapshot", json={"name": "before"}).status_code == 200
    assert client.post("/api/labs/office/explode").status_code == 400
    link = "/api/labs/office/nodes/r1/links/lan"
    assert client.post(link, json={"state": "down"}).json()["up"] is False
    assert (
        client.post(link, json={"delay": "50ms", "loss": "2.5"}).json()["shaping"] == "delay 50ms"
    )
    assert client.post(link, json={}).status_code == 200

    assert calls == [
        ("snap", "office", "before"),
        ("state", "r1", "lan", False),
        ("shape", "r1", "lan", "50ms", 2.5),
        ("shape", "r1", "lan", None, None),
    ]


def test_scenario_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web.scenario, "active_scenarios", lambda: {"nat": "nat"})

    listing = client.get("/api/scenarios").json()
    assert [item["name"] for item in listing][:2] == ["static", "routing"]
    assert next(item for item in listing if item["name"] == "nat")["running"] is True

    detail = client.get("/api/scenario", params={"ref": "dns"}).json()
    assert detail["title"] == "DNS for an internal domain"
    assert detail["task"].startswith("# Scenario 5")
    assert len(detail["hints"]) >= 3

    assert client.get("/api/scenario", params={"ref": "nope"}).status_code == 400


def test_page_and_static_files_are_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "<title>labnet</title>" in page.text
    for file in ("app.js", "style.css", "vendor/xterm.js", "vendor/cytoscape.min.js"):
        assert client.get(f"/static/{file}").status_code == 200, file
