"""Searching generated documents for disagreements, one atom at a time.

The documents come from Hypothesis, which also shrinks each disagreement to the
simplest document that shows it.  Hypothesis is built to fail a test, not to
survey, so the search runs in rounds, one atom to a round.  A round's first new
atom becomes its target, and from then on only a document showing that atom
fails; every other atom passes, the ones earlier rounds found and any new one
alike.  The search ends with the first round that gets through its examples
without a new atom.

Both halves of that are load-bearing.  Hypothesis shrinks toward any failure
at all when it reports one bug -- a "slip", in its own source's word -- and
deleting a date heading turns most disagreements into the one about undated
links, so without a single target a round would report a different atom from
the one it found, and the one it found might never be reported.  And one bug a
round, rather than Hypothesis's multiple-bug reporting, because that searches
on for up to ten seconds of wall-clock time after its first bug, so a seed
would not repeat a run on another machine.  A seed still depends on the clock
in two places Hypothesis and this module both keep: a document that takes
longer than the timeout on one machine and not another, and Hypothesis's
five-minute limit on shrinking one find.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
from hypothesis.strategies import SearchStrategy

from hbt.fuzz.verdict import Atom, Verdict, atoms, execute

# The name every document is written under, in a directory of its own.
INPUT = "input"


class Disagreement(Exception):
    """A document showing the atom a round is after."""

    def __init__(self, text: str, verdicts: dict[str, Verdict], atom: Atom) -> None:
        super().__init__(atom.describe())
        self.text = text
        self.verdicts = verdicts
        self.atom = atom


class Trial:
    """Runs the implementations over documents, and raises what they disagree on.

    Handed each implementation's binary by name; which implementations exist,
    and where their binaries come from, is the business of whoever drives this.
    """

    def __init__(self, binaries: Mapping[str, Path], suffix: str, timeout: float, scratch: Path) -> None:
        # Absolute, because they run from the scratch directory: a relative
        # --binary names a path from where the command was run.
        self.binaries = {name: binary.absolute() for name, binary in binaries.items()}
        self.timeout = timeout
        self.path = scratch / (INPUT + suffix)
        self.pool = ThreadPoolExecutor(max_workers=len(self.binaries))
        # Found in an earlier round, and so passed from now on.
        self.known: set[Atom] = set()
        # The atom this round is after, once it has found one.
        self.target: Atom | None = None
        # By text, because many of Hypothesis's choices build the same
        # document -- shrinking most of all -- and running the implementations
        # is nearly all of the cost. The price is that Hypothesis's replay of a
        # find reads this rather than running them again, so it no longer
        # notices an implementation that answers one document two ways.
        self.cache: dict[str, dict[str, Verdict]] = {}

    def judge(self, text: str) -> dict[str, Verdict]:
        """What each implementation makes of `text`, run side by side.

        Always the same file, which is safe because Hypothesis runs one
        document at a time.
        """
        if text not in self.cache:
            self.path.write_text(text, encoding="utf-8")
            futures = {
                impl: self.pool.submit(execute, binary, self.path.parent, self.path.name, self.timeout)
                for impl, binary in self.binaries.items()
            }
            self.cache[text] = {impl: future.result() for impl, future in futures.items()}
        return self.cache[text]

    def check(self, text: str) -> None:
        """Raise a :class:`Disagreement` if this document shows the round's target atom.

        Until the round has a target, its first document with a new atom sets
        it, to the least of them, so that one document always picks the same.
        """
        verdicts = self.judge(text)
        found = atoms(verdicts)
        if self.target is None:
            new = sorted(found - self.known)
            if not new:
                return
            self.target = new[0]
        if self.target in found:
            raise Disagreement(text, verdicts, self.target)


def search(
    trial: Trial, strategy: SearchStrategy[str], *, examples: int, shrink: bool, seed_value: int
) -> list[Disagreement]:
    """Every atom found, each with its shrunk document, in the order found."""
    found: list[Disagreement] = []
    while (new := search_round(trial, strategy, examples, shrink, f"{seed_value}/{len(found)}")) is not None:
        found.append(new)
        trial.known.add(new.atom)
        trial.target = None
    return found


def search_round(
    trial: Trial, strategy: SearchStrategy[str], examples: int, shrink: bool, seed_value: str
) -> Disagreement | None:
    """The first new atom one Hypothesis run finds, with its shrunk document.

    Quiet, with no example database and no deadline: the report is the
    caller's to print; a database would replay one run's finds into the next,
    which a seeded run should not; and each example runs a process per
    implementation, which is exactly what the deadline and the too-slow health
    check are there to complain about.
    """
    phases = [Phase.generate, Phase.shrink] if shrink else [Phase.generate]

    @seed(seed_value)
    @settings(
        max_examples=examples,
        database=None,
        deadline=None,
        phases=phases,
        verbosity=Verbosity.quiet,
        print_blob=False,
        report_multiple_bugs=False,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(strategy)
    def probe(text: str) -> None:
        trial.check(text)

    try:
        # Hypothesis supplies `text`; pylint reads the undecorated signature.
        probe()  # pylint: disable=no-value-for-parameter
    except Disagreement as exc:
        return exc
    return None
