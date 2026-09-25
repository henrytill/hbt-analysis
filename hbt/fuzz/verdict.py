"""What each implementation makes of a document, and the ways those verdicts disagree.

A document's disagreement is taken apart into :class:`Atom`\\ s -- these
implementations fail with this message; this field splits them this way -- and
an atom, not a document, is what is reported.  A document that differs in a
label and in a name is two disagreements that happen to share an input; were
it one, every combination of independent disagreements would be a kind of its
own, and there would be two to the power of their number.
"""

from __future__ import annotations

import itertools
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hbt.conformance import NormalizationError
from hbt.conformance.runner import FORMATS


@dataclass(frozen=True)
class Failure:
    """An implementation that did not produce a Collection, and why.

    `detail` is everything it wrote to stderr, so that two failures compare
    equal when they are the same failure.  All of it rather than one line:
    which line says why differs by implementation, and an environment with
    RUST_BACKTRACE set puts a stack trace after hbt-rs's message.  Every
    document is run as the same relative path from the same directory, so a
    diagnostic that names it names it the same way each time.
    """

    reason: str
    detail: tuple[str, ...] = ()


# What one implementation made of one document: the normalized Collection, or
# the failure.
Verdict = dict[str, Any] | Failure

# Implementations grouped, each group sorted and the groups in the order their
# first member sorts, so that one grouping has one spelling.
Split = tuple[tuple[str, ...], ...]


@dataclass(frozen=True, order=True)
class Atom:
    """One way the implementations disagree: the unit that is found, shrunk and reported.

    `kind` is "fails", with `subject` the failure and `split` holding the one
    group that failed that way while others did not; or "differs", with
    `subject` the path of a field, its indices blanked, and `split` the groups
    of implementations that agree on it.
    """

    kind: str
    subject: str
    split: Split

    def describe(self) -> str:
        """The atom in a line."""
        if self.kind == "fails":
            return f"{', '.join(self.split[0])} fail(s) where the rest do not"
        return f"{self.subject} splits them {' | '.join(', '.join(g) for g in self.split)}"


def execute(binary: Path, directory: Path, name: str, timeout: float) -> Verdict:
    """Run one implementation over the document `name` in `directory`, as the harness runs a fixture."""
    try:
        proc = subprocess.run(
            [str(binary), "-t", "yaml", name], cwd=directory, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return Failure(f"timed out after {timeout:g}s")
    except OSError as exc:
        return Failure(f"could not run {binary}: {exc}")
    if proc.returncode != 0:
        # UTF-8 whatever the locale says, as hbt.conformance reads output: a
        # LANG-less C locale would otherwise decode it as ASCII.
        stderr = proc.stderr.decode("utf-8", errors="replace")
        return Failure(f"exit {proc.returncode}", tuple(line.rstrip() for line in stderr.strip().splitlines()))
    try:
        document: dict[str, Any] = FORMATS["yaml"].parse(proc.stdout)
    # What the harness itself catches when it parses output, and PyYAML's
    # recursion limit on pathological nesting, which is as much the
    # implementation's doing. Anything else is a bug here, not a verdict.
    except (NormalizationError, yaml.YAMLError, RecursionError) as exc:
        return Failure(f"the output is not a Collection: {exc}")
    return document


def group(names: Iterable[str], together: Callable[[str, str], bool]) -> Split:
    """Group `names`, putting each with the first group whose first member it is `together` with."""
    groups: list[list[str]] = []
    for name in sorted(names):
        for members in groups:
            if together(members[0], name):
                members.append(name)
                break
        else:
            groups.append([name])
    return tuple(tuple(members) for members in groups)


def _field(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def _agreeing_on(field: str, differing: Mapping[tuple[str, str], set[str]]) -> Callable[[str, str], bool]:
    return lambda a, b: field not in differing[a, b]


def atoms(verdicts: Mapping[str, Verdict]) -> set[Atom]:
    """Every way the verdicts disagree.

    Any two failures agree, so a failure is an atom only when some other
    implementation succeeded; implementations that fail with the same message
    are one atom.  A field is an atom when it splits the implementations that
    succeeded, grouped by whether they differ on it -- so an implementation
    that differs from three others is one atom, not three.
    """
    failed = {name: v for name, v in verdicts.items() if isinstance(v, Failure)}
    documents = {name: v for name, v in verdicts.items() if not isinstance(v, Failure)}
    found: set[Atom] = set()
    if documents:
        for members in group(failed, lambda a, b: failed[a] == failed[b]):
            failure = failed[members[0]]
            found.add(Atom("fails", "\n".join([failure.reason, *failure.detail]), (members,)))
    differing: dict[tuple[str, str], set[str]] = {}
    for a, b in itertools.combinations(sorted(documents), 2):
        differing[a, b] = {_field(d.path) for d in FORMATS["yaml"].diff(documents[a], documents[b])}
    for field in set[str]().union(*differing.values()):
        found.add(Atom("differs", field, group(documents, _agreeing_on(field, differing))))
    return found


def split(verdicts: Mapping[str, Verdict]) -> Split:
    """The implementations grouped by agreement: two agree when there is no atom between them."""
    return group(verdicts, lambda a, b: not atoms({a: verdicts[a], b: verdicts[b]}))
