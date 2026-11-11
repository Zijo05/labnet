"""The web UI backend.

A thin HTTP layer over the same engine the CLI uses: every endpoint calls a
function from ``labnet.engine`` or ``labnet.scenario`` and returns the result
as JSON. There is no lab logic in this file.

Security model, in short: the terminal endpoint gives root shells on lab
nodes, so the server listens on 127.0.0.1 only by default, and every request
is checked by ``is_trusted_request`` so that another website open in the
user's browser cannot drive it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from labnet import __version__, engine, scenario
from labnet.errors import LabnetError
from labnet.web.terminal import NodeTerminal

STATIC_DIR = Path(__file__).parent / "static"

# Host names under which a browser on this machine reaches the server.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


class _RevalidatedStaticFiles(StaticFiles):
    """Static files that browsers must re-check before using a cached copy.

    Without this a browser keeps running the old JavaScript after labnet has
    been upgraded. The check is cheap: unchanged files answer "not modified".
    """

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _hostname(host: str) -> str:
    """The name part of a Host header: ``localhost:8080`` gives ``localhost``."""
    if host.startswith("["):  # IPv6 literal, like [::1]:8080
        return host.split("]", 1)[0].lower() + "]"
    return host.split(":", 1)[0].lower()


def is_trusted_request(host: str | None, origin: str | None, allowed_hosts: frozenset[str]) -> bool:
    """Decide whether a request really comes from our own page.

    Two attacks are blocked here, both of which would otherwise let any
    website the user visits reach this server through the user's browser:

    * Cross-site requests. A page on another site can make the browser send
      requests (including WebSocket connections) to http://127.0.0.1:8080.
      The browser attaches an ``Origin`` header naming the page's site, so a
      request is refused when ``Origin`` is not this server itself.
    * DNS rebinding. An attacker's domain can be made to resolve to
      127.0.0.1, which makes the browser treat the attacker's page as "same
      origin". The ``Host`` header then names the attacker's domain, so only
      known host names are accepted.
    """
    if not host or _hostname(host) not in allowed_hosts:
        return False
    if origin is None:
        return True  # not sent by a browser page on another site (curl, a typed-in URL)
    return origin.split("://", 1)[-1] == host


def create_app(extra_hosts: frozenset[str] = frozenset()) -> FastAPI:
    """Build the web application.

    ``extra_hosts`` are additional host names or addresses the server may be
    reached by, used only when the user deliberately binds to a non-local
    address.
    """
    allowed_hosts = LOCAL_HOSTS | extra_hosts
    app = FastAPI(title="labnet", version=__version__, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def reject_untrusted(request: Request, call_next: Any) -> Any:
        headers = request.headers
        if not is_trusted_request(headers.get("host"), headers.get("origin"), allowed_hosts):
            return JSONResponse({"error": "Request refused: not from the labnet page."}, 403)
        return await call_next(request)

    @app.exception_handler(LabnetError)
    async def labnet_error(_request: Request, exc: LabnetError) -> JSONResponse:
        return JSONResponse({"error": exc.message, "hint": exc.hint}, status_code=400)

    # --- labs ---------------------------------------------------------------

    @app.get("/api/labs")
    def list_labs(detail: bool = False) -> list[dict[str, Any]]:
        """All labs. With ``detail`` the link delay/loss settings are included."""
        labs = engine.read_labs()
        if detail:
            for lab in labs.values():
                for node in lab.nodes:
                    engine.read_shaping(node)
        return [asdict(lab) for lab in labs.values()]

    @app.delete("/api/labs/{lab}")
    def delete_lab(lab: str) -> dict[str, Any]:
        removed = engine.down(lab)
        return {"removed": removed.name}

    @app.post("/api/labs/{lab}/{action}")
    def lab_action(
        lab: str, action: str, body: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        name = str(body.get("name") or engine.DEFAULT_SNAPSHOT)
        actions = {
            "start": lambda: engine.start(lab),
            "stop": lambda: engine.stop(lab),
            "snapshot": lambda: engine.snapshot(lab, name),
            "restore": lambda: engine.restore(lab, name),
        }
        if action not in actions:
            raise LabnetError(f"Unknown lab action '{action}'.")
        return {"action": action, "nodes": actions[action]()}

    @app.post("/api/labs/{lab}/nodes/{node}/{action}")
    def node_action(lab: str, node: str, action: str) -> dict[str, Any]:
        if action == "start":
            return {"nodes": engine.start(lab, node)}
        if action == "stop":
            return {"nodes": engine.stop(lab, node)}
        raise LabnetError(f"Unknown node action '{action}'.")

    @app.post("/api/labs/{lab}/nodes/{node}/links/{network}")
    def link_action(
        lab: str, node: str, network: str, body: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        """Unplug or re-plug a cable (``state``), or set delay and loss."""
        state = engine.get_lab(lab).node(node)
        if "state" in body:
            interface = engine.set_link_state(state, network, up=body["state"] == "up")
        else:
            loss = body.get("loss")
            interface = engine.shape_link(
                state,
                network,
                delay=body.get("delay") or None,
                loss=float(loss) if loss not in (None, "") else None,
            )
        return asdict(interface)

    # --- scenarios ----------------------------------------------------------

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        running = set(scenario.active_scenarios().values())
        return [
            {
                "name": item.name,
                "number": item.number,
                "title": item.title,
                "summary": item.summary,
                "lab": item.lab,
                "running": item.reference in running,
            }
            for item in scenario.list_scenarios()
        ]

    # A scenario is identified by ``ref``: a built-in name such as "nat", or
    # the path of a scenario directory. It is a query parameter because a
    # path contains slashes.

    @app.get("/api/scenario")
    def get_scenario(ref: str) -> dict[str, Any]:
        item = scenario.load_scenario(ref)
        return {
            "name": item.name,
            "number": item.number,
            "title": item.title,
            "lab": item.lab,
            "task": item.task,
            "hints": item.hints,
            "checks": [check.name for check in item.checks],
        }

    @app.post("/api/scenario/start")
    def start_scenario(ref: str) -> dict[str, Any]:
        item = scenario.start(ref)
        return {"name": item.name, "lab": item.lab}

    @app.post("/api/scenario/reset")
    def reset_scenario(ref: str) -> dict[str, Any]:
        item = scenario.reset(ref)
        return {"name": item.name, "lab": item.lab}

    @app.post("/api/scenario/check")
    def check_scenario(ref: str, wait: bool = True) -> dict[str, Any]:
        item = scenario.find_active(ref)
        results = scenario.run_checks(item, patient=wait)
        return {
            "passed": sum(1 for result in results if result.passed),
            "total": len(results),
            "results": [
                {
                    "name": result.check.name,
                    "node": result.check.node,
                    "passed": result.passed,
                    "detail": result.detail,
                    "explain": result.check.explain,
                }
                for result in results
            ],
        }

    # --- terminal -----------------------------------------------------------

    @app.websocket("/ws/terminal/{lab}/{node}")
    async def terminal(websocket: WebSocket, lab: str, node: str) -> None:
        headers = websocket.headers
        if not is_trusted_request(headers.get("host"), headers.get("origin"), allowed_hosts):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        try:
            state = await asyncio.to_thread(lambda: engine.get_lab(lab).node(node))
            engine.require_running(state)
        except LabnetError as exc:
            await websocket.send_bytes(f"\r\n{exc.message}\r\n{exc.hint}\r\n".encode())
            await websocket.close()
            return
        await _relay_terminal(websocket, NodeTerminal(engine.shell_argv(state)))

    # --- pages --------------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", _RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    return app


async def _relay_terminal(websocket: WebSocket, session: NodeTerminal) -> None:
    """Copy bytes between a WebSocket and a node terminal until either side ends.

    Node output goes to the browser as binary frames. The browser sends small
    JSON text frames: ``{"type": "input", "data": "ls\\r"}`` for keystrokes
    and ``{"type": "resize", "rows": 30, "cols": 100}`` when its size changes.
    """
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes] = asyncio.Queue()

    def on_readable() -> None:
        # Called by the event loop whenever the PTY has something to read.
        data = session.read()
        if not data:
            loop.remove_reader(session.fd)  # the shell exited
        output.put_nowait(data)

    async def send_output() -> None:
        while True:
            data = await output.get()
            if not data:
                await websocket.close()
                return
            await websocket.send_bytes(data)

    loop.add_reader(session.fd, on_readable)
    sender = asyncio.create_task(send_output())
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "input":
                session.write(str(event.get("data", "")).encode())
            elif event.get("type") == "resize":
                session.resize(int(event.get("rows", 24)), int(event.get("cols", 80)))
    except RuntimeError:
        pass  # the socket was closed by send_output after the shell exited
    finally:
        with contextlib.suppress(ValueError, OSError):
            loop.remove_reader(session.fd)
        sender.cancel()
        session.close()


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the web UI until interrupted."""
    import uvicorn

    extra = frozenset() if host in ("127.0.0.1", "localhost", "::1") else frozenset({host})
    uvicorn.run(create_app(extra), host=host, port=port, log_level="warning")
