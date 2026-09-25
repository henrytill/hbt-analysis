"""Stand-ins for an implementation, shared by the tests that run one.

A shell script in a temporary directory is enough of an `hbt` for what the
commands do with one: run it, read its stdout and stderr, and look at its exit
status.
"""

from __future__ import annotations

import stat
from pathlib import Path

# The empty Collection, as an implementation writes it.
DOCUMENT = """version: 0.1.0
length: 0
value: []
"""


def executable(root: Path, name: str, script: str) -> str:
    """Write `script` as an executable shell script under `root`, and return its path."""
    path = root / name
    path.write_text(f"#!/bin/sh\n{script}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)
