"""The results document: its format version, and reading and writing it.

The results are the durable artifact of a benchmark; rendering is a separate
step that reads them back, so a report can be regenerated without re-running
anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Bumped when the shape of the results document changes incompatibly.  The
# thing being benchmarked versions its own serialized Collection against
# ^0.1.0; this file is committed, re-rendered by a Nix derivation on every
# push, and explicitly meant to be re-read later, so it gets the same
# treatment rather than being read with bare subscripts forever.
FORMAT_VERSION = "0.1.0"


class BenchmarkError(Exception):
    """A condition that should stop a benchmark with a message, not a traceback.

    This library's own, so that it knows nothing of the command driving it;
    `hbt-analysis` treats it as a refusal.
    """


def load_results(path: Path) -> dict[str, Any]:
    """Read back a results document, translating the failures load_corpus does.

    The document's shape is this module's contract, so reading it is too. It
    matters here because the path that re-renders a saved run is the one the
    Nix derivation for the published page takes: a missing or malformed file
    should fail that build with a message, not a traceback.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except FileNotFoundError as exc:
        raise BenchmarkError(f"{path}: no such results file") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"{path}: not valid JSON: {exc}") from exc
    return data


def dump_results(data: dict[str, Any]) -> str:
    """The document's on-disk form, so only this module spells it."""
    return json.dumps(data, indent=2) + "\n"
