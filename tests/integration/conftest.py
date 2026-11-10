"""Shared helpers for integration tests.

These tests create real Incus containers and networks. Every lab they build
has a name starting with ``it`` and is removed again afterwards. They are
skipped automatically on a machine without a working Incus.

Run them with:  ./dev.sh integration
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from labnet import engine

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip every integration test when Incus is missing or unreachable."""
    if shutil.which("incus") is not None:
        reachable = subprocess.run(["incus", "query", "/1.0"], capture_output=True, check=False)
        if reachable.returncode == 0:
            return
    skip = pytest.mark.skip(reason="needs a working Incus (see 'labnet install-check')")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def labnet(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the labnet CLI exactly as a user would and capture its output."""
    return subprocess.run(
        [sys.executable, "-m", "labnet", *args],
        input=stdin,
        stdin=subprocess.DEVNULL if stdin is None else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=1200,
    )


def on_node(node: str, command: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command on a lab node through the CLI."""
    return labnet("exec", node, "--", "sh", "-c", command)


def remove_lab(name: str) -> None:
    """Delete a lab if it exists (used for cleanup, so never fails the test)."""
    if name in engine.read_labs():
        engine.down(name)


@pytest.fixture
def make_lab(tmp_path: Path) -> Iterator[Callable[[str, str], str]]:
    """Return a function that writes a topology file, builds it, and cleans up later.

    Usage: ``lab_file = make_lab("itcore", "networks: ...")``. The lab name is
    added to the YAML for you.
    """
    created: list[str] = []

    def _make(name: str, body: str) -> str:
        remove_lab(name)
        path = tmp_path / f"{name}.yaml"
        path.write_text(f"name: {name}\n{body}", encoding="utf-8")
        created.append(name)
        result = labnet("up", str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        return str(path)

    yield _make
    for name in created:
        remove_lab(name)
