"""Rendering a disagreement: who disagrees with whom, how, and the document that shows it."""

from __future__ import annotations

from typing import Any, TextIO

from hbt.conformance.runner import FORMATS
from hbt.fuzz.search import Disagreement
from hbt.fuzz.verdict import Failure, Verdict, split

# Differences shown per pair of classes. The first few say what kind of
# disagreement it is; the input says the rest.
SHOWN = 5

# Lines of a failure's stderr shown. The first few name the error; a backtrace
# after them says nothing a report reader needs.
STDERR = 4


def _describe(verdict: Verdict) -> list[str]:
    if not isinstance(verdict, Failure):
        return [f"{verdict['length']} node(s)"]
    lines = [line for line in verdict.detail if line.strip()]
    shown = [f"failed: {verdict.reason}", *(f"  {line}" for line in lines[:STDERR])]
    return shown + ([f"  ... and {len(lines) - STDERR} more line(s)"] if len(lines) > STDERR else [])


def _differences(succeeded: list[tuple[str, dict[str, Any]]], out: TextIO) -> None:
    """How each group with a Collection differs from the first such group.

    The first to succeed rather than the first group: when that one failed
    there would be nothing to compare the rest with.
    """
    for name, verdict in succeeded[1:]:
        first, reference = succeeded[0]
        print(f"  {name} against {first}:", file=out)
        differences = FORMATS["yaml"].diff(reference, verdict)
        for difference in differences[:SHOWN]:
            for line in difference.render().splitlines():
                print(f"      {line}", file=out)
        if len(differences) > SHOWN:
            print(f"      ... and {len(differences) - SHOWN} more", file=out)


def report(index: int, found: Disagreement, out: TextIO) -> None:
    """One atom, with the simplest document that shows it and everything else that document shows."""
    groups = split(found.verdicts)
    print(file=out)
    print(f"disagreement {index}: {found.atom.describe()}", file=out)
    representatives = [found.verdicts[members[0]] for members in groups]
    for members, verdict in zip(groups, representatives):
        head, *rest = _describe(verdict)
        print(f"  {', '.join(members)}: {head}", file=out)
        for line in rest:
            print(f"    {line}", file=out)
    _differences([(m[0], v) for m, v in zip(groups, representatives) if not isinstance(v, Failure)], out)
    print("  input:", file=out)
    for line in found.text.splitlines() or [""]:
        print(f"    | {line}".rstrip(), file=out)
