"""Read the executable steps out of a written reference solution.

The reference solutions in ``docs/solutions/`` are Markdown for people. Code
blocks that should be run on a node are marked with the node's name::

    ```sh node=r1
    systemctl enable --now nftables
    ```

Blocks without ``node=`` are explanation only. The tests run exactly the
marked blocks, so the documented solution and the tested solution cannot
drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

SOLUTIONS_DIR = Path(__file__).resolve().parents[1] / "docs" / "solutions"

_BLOCK_RE = re.compile(r"^```\w+[ \t]+node=(\S+)[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)


def solution_file(number: int, name: str) -> Path:
    """Path of the solution document for a scenario."""
    return SOLUTIONS_DIR / f"{number:02d}-{name}.md"


def solution_steps(number: int, name: str) -> list[tuple[str, str]]:
    """Return the (node, shell script) steps of a solution, in document order."""
    text = solution_file(number, name).read_text(encoding="utf-8")
    return [(match.group(1), match.group(2)) for match in _BLOCK_RE.finditer(text)]
