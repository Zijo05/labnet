"""A thin wrapper around the ``incus`` command-line tool.

labnet drives Incus by running the ``incus`` CLI and reading its JSON output
(see docs/design-decisions.md for why not the REST API or a library). Every
Incus call in the project goes through this module, so there is one place to
look when something about Incus changes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import yaml

from labnet.errors import LabnetError


class IncusError(LabnetError):
    """An ``incus`` command failed."""


class IncusTimeout(IncusError):
    """An ``incus`` command did not finish in time."""


def run(
    args: list[str],
    *,
    stdin: str | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``incus <args>`` and return the finished process.

    With ``check=True`` (the default) a non-zero exit raises ``IncusError``
    carrying Incus's own error text. Output is captured as text. When no
    ``stdin`` text is given the command reads from /dev/null, so it can never
    swallow what the user types into the terminal.
    """
    if shutil.which("incus") is None:
        raise IncusError(
            "The 'incus' command was not found.",
            hint="Install Incus first. Run 'labnet install-check' for step-by-step help.",
        )
    try:
        result = subprocess.run(
            ["incus", *args],
            input=stdin,
            stdin=subprocess.DEVNULL if stdin is None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise IncusTimeout(
            f"'incus {' '.join(args[:3])} ...' did not finish within {timeout:.0f} seconds."
        ) from None
    if check and result.returncode != 0:
        message, hint = _explain_failure(args, result.stderr.strip() or result.stdout.strip())
        raise IncusError(message, hint=hint)
    return result


def _explain_failure(args: list[str], stderr: str) -> tuple[str, str]:
    """Turn a failed incus command into a (message, hint) pair."""
    command = "incus " + " ".join(args[:4]) + (" ..." if len(args) > 4 else "")
    lowered = stderr.lower()
    if "permission denied" in lowered and "socket" in lowered:
        return (
            "Incus refused the connection: permission denied on its socket.",
            "Add your user to the 'incus-admin' group with "
            "'sudo usermod -aG incus-admin $USER', then log out and back in "
            "(or run 'newgrp incus-admin').",
        )
    if "connection refused" in lowered or ("unix.socket" in lowered and "no such file" in lowered):
        return (
            "Cannot reach the Incus daemon.",
            "Check that it is running with 'systemctl status incus'. "
            "On WSL, systemd must be enabled; see docs/install.md.",
        )
    return (f"Incus command failed: {command}\n  Incus said: {stderr}", "")


def query_json(args: list[str]) -> Any:
    """Run an incus listing command with ``--format json`` and parse the result."""
    result = run([*args, "--format", "json"])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise IncusError(
            f"Incus returned output that is not JSON for: incus {' '.join(args)}"
        ) from None


# --- reading state ---------------------------------------------------------


def list_instances() -> list[dict[str, Any]]:
    """All instances with their config and live state (addresses, interfaces)."""
    return query_json(["list"])


def list_networks() -> list[dict[str, Any]]:
    """All networks Incus knows about, managed or not."""
    return query_json(["network", "list"])


def list_images() -> list[dict[str, Any]]:
    """All images in the local image store."""
    return query_json(["image", "list"])


def list_storage_pools() -> list[dict[str, Any]]:
    """All storage pools."""
    return query_json(["storage", "list"])


def server_info() -> dict[str, Any]:
    """Server environment details (version, firewall driver, ...)."""
    return json.loads(run(["query", "/1.0"]).stdout)


def default_storage_pool() -> str:
    """Pick the storage pool for container root disks.

    Uses the pool called ``default`` when there is one (``incus admin init``
    creates it), otherwise the first pool.
    """
    pools = [pool["name"] for pool in list_storage_pools()]
    if not pools:
        raise IncusError(
            "Incus has no storage pool yet.",
            hint="Initialise Incus once with 'sudo incus admin init --minimal'.",
        )
    return "default" if "default" in pools else pools[0]


# --- changing state --------------------------------------------------------


def create_network(name: str, config: dict[str, str], description: str = "") -> None:
    """Create a bridge network with the given config keys."""
    args = ["network", "create", name]
    if description:
        args += ["--description", description]
    args += [f"{key}={value}" for key, value in config.items()]
    run(args)


def delete_network(name: str) -> None:
    """Delete a network."""
    run(["network", "delete", name])


def init_instance(
    name: str, image: str, config: dict[str, str], devices: dict[str, dict[str, str]]
) -> None:
    """Create (but do not start) a container.

    ``--no-profiles`` keeps the container independent of whatever the user's
    ``default`` profile contains. Its whole definition (config keys and
    devices) is passed as YAML on standard input, so one command creates it.
    """
    definition = yaml.safe_dump({"config": config, "devices": devices})
    run(["init", image, name, "--no-profiles", "--quiet"], stdin=definition, timeout=900)


def start_instances(names: list[str]) -> None:
    """Start one or more instances."""
    if names:
        run(["start", *names], timeout=300)


def stop_instances(names: list[str], force: bool = True) -> None:
    """Stop one or more instances. ``force`` skips the clean shutdown wait."""
    if names:
        run(["stop", *names, *(["--force"] if force else [])], timeout=300)


def delete_instance(name: str) -> None:
    """Delete an instance, stopping it first if needed."""
    run(["delete", name, "--force"], timeout=300)


def exec_in(
    instance: str,
    command: list[str],
    *,
    stdin: str | None = None,
    timeout: float | None = 60,
) -> subprocess.CompletedProcess[str]:
    """Run a command inside an instance and capture its output.

    A non-zero exit does not raise: callers look at ``returncode`` themselves,
    because for checks and probes a failing command is a normal answer. A
    timeout is reported the same way, with exit code 124 (as ``timeout(1)``
    does).
    """
    try:
        return run(["exec", instance, "--", *command], stdin=stdin, check=False, timeout=timeout)
    except IncusTimeout as exc:
        return subprocess.CompletedProcess(["incus", "exec"], 124, "", exc.message)
