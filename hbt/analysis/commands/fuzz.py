"""Holding the implementations to each other on inputs nobody wrote expectations for.

`conformance` holds each implementation to the corpus, so it can only find a
disagreement someone already thought to pin.  This generates documents, runs
every implementation over each, and reports the ones they do not all read the
same way -- which is where the next fixture comes from: a disagreement found
here is settled in hbt-data, pinned there, and from then on conformance holds
everyone to it.

No implementation is the reference.  Which side of a disagreement is right is
the question it raises, not one this answers.  Agreement is `hbt.conformance`'s
own comparison, imported rather than restated, so two outputs this calls the
same would pass as each other's expectation.  Any two failures agree: the
implementations word their errors differently, and whether a document is
refused is the question, not how.

A document's disagreement is taken apart into :class:`Atom`\\ s -- these
implementations fail with this message; this field splits them this way -- and
an atom, not a document, is what is reported.  A document that differs in a
label and in a name is two disagreements that happen to share an input; were
it one, every combination of independent disagreements would be a kind of its
own, and there would be two to the power of their number.

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
in two places Hypothesis and this command both keep: a document that takes
longer than `--timeout` on one machine and not another, and Hypothesis's
five-minute limit on shrinking one find.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import click
import yaml
from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
from hypothesis.strategies import SearchStrategy

from hbt.analysis.commands import CommandError, invoke, print_table, selection_options
from hbt.analysis.generators import GENERATORS
from hbt.analysis.implementations import Impl, Selection, choose, discover, repo_root
from hbt.conformance import NormalizationError
from hbt.conformance.runner import FORMATS

# A fuzzed document is a few dozen lines; anything slower than this is a hang.
DEFAULT_TIMEOUT = 10.0

# Differences shown per pair of classes. The first few say what kind of
# disagreement it is; the input says the rest.
SHOWN = 5

# Lines of a failure's stderr shown. The first few name the error; a backtrace
# after them says nothing a report reader needs.
STDERR = 4

# The name every document is written under, in a directory of its own.
INPUT = "input"


@dataclass(frozen=True)
class Options:
    """Everything `fuzz` alone is told; which implementations is a :class:`Selection`."""

    format: str = "markdown"
    examples: int = 200
    seed: int | None = None
    timeout: float = DEFAULT_TIMEOUT
    shrink: bool = True
    keep: Path | None = None


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


class Disagreement(Exception):
    """A document showing the atom a round is after."""

    def __init__(self, text: str, verdicts: dict[str, Verdict], atom: Atom) -> None:
        super().__init__(atom.describe())
        self.text = text
        self.verdicts = verdicts
        self.atom = atom


class Trial:
    """Runs the implementations over documents, and raises what they disagree on."""

    def __init__(self, impls: list[Impl], suffix: str, timeout: float, scratch: Path) -> None:
        # Absolute, because they run from the scratch directory: a relative
        # --binary names a path from where the command was run.
        self.binaries = {impl.name: impl.binary.absolute() for impl in impls if impl.binary is not None}
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


def search(trial: Trial, strategy: SearchStrategy[str], options: Options, seed_value: int) -> list[Disagreement]:
    """Every atom found, each with its shrunk document, in the order found."""
    found: list[Disagreement] = []
    while (new := search_round(trial, strategy, options, f"{seed_value}/{len(found)}")) is not None:
        found.append(new)
        trial.known.add(new.atom)
        trial.target = None
    return found


def search_round(trial: Trial, strategy: SearchStrategy[str], options: Options, seed_value: str) -> Disagreement | None:
    """The first new atom one Hypothesis run finds, with its shrunk document.

    Quiet, with no example database and no deadline: the report is this
    command's to print; a database would replay one run's finds into the next,
    which a seeded run should not; and each example runs a process per
    implementation, which is exactly what the deadline and the too-slow health
    check are there to complain about.
    """
    phases = [Phase.generate, Phase.shrink] if options.shrink else [Phase.generate]

    @seed(seed_value)
    @settings(
        max_examples=options.examples,
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


def _describe(verdict: Verdict) -> list[str]:
    if not isinstance(verdict, Failure):
        return [f"{verdict['length']} node(s)"]
    lines = [line for line in verdict.detail if line.strip()]
    shown = [f"failed: {verdict.reason}", *(f"  {line}" for line in lines[:STDERR])]
    return shown + ([f"  ... and {len(lines) - STDERR} more line(s)"] if len(lines) > STDERR else [])


def _header(seed_value: int, options: Options, impls: list[Impl], out: TextIO) -> None:
    """What ran: the seed that reproduces it, and each implementation's binary and build."""
    print(f"seed      {seed_value}", file=out)
    print(f"format    {options.format}", file=out)
    print(f"examples  {options.examples} per round", file=out)
    print(file=out)
    rows = [
        (
            (impl.name, f"unavailable: {impl.error}", "")
            if impl.binary is None
            else (impl.name, str(impl.binary), impl.build or "-")
        )
        for impl in impls
    ]
    print_table(rows, out)


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


def _report(index: int, found: Disagreement, out: TextIO) -> None:
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


def _keep(directory: Path, stem: str, found: list[Disagreement], suffix: str) -> None:
    """Write each disagreement's input where it can be turned into a fixture."""
    directory.mkdir(parents=True, exist_ok=True)
    for index, disagreement in enumerate(found, start=1):
        (directory / f"{stem}-{index}.input{suffix}").write_text(disagreement.text, encoding="utf-8")


def run(selection: Selection, options: Options, out: TextIO) -> int:
    """Fuzz the selected implementations; 0 if they agreed on every document."""
    root = repo_root()
    impls = discover(root, *choose(root, selection)[1:])
    if sum(impl.binary is not None for impl in impls) < 2:
        raise CommandError("fuzzing needs at least two implementations with a binary")
    generator = GENERATORS[options.format]
    seed_value = options.seed if options.seed is not None else int.from_bytes(os.urandom(4))
    _header(seed_value, options, impls, out)

    with tempfile.TemporaryDirectory(prefix="hbt-fuzz-") as scratch:
        trial = Trial(impls, generator.suffix, options.timeout, Path(scratch))
        try:
            found = search(trial, generator.strategy, options, seed_value)
        finally:
            trial.pool.shutdown()

    for index, disagreement in enumerate(found, start=1):
        _report(index, disagreement, out)
    if options.keep is not None:
        _keep(options.keep, f"fuzz-{seed_value}", found, generator.suffix)

    print(file=out)
    if not found:
        print("no disagreement found", file=out)
        return 0
    print(f"{len(found)} disagreement(s) found", file=out)
    return 1


@click.command()
@selection_options
@click.option(
    "--format",
    type=click.Choice(sorted(GENERATORS)),
    default="markdown",
    show_default=True,
    help="the input format to generate",
)
@click.option(
    "-n",
    "--examples",
    type=click.IntRange(min=1),
    default=200,
    show_default=True,
    help="documents to generate per round, not counting the ones shrinking tries",
)
@click.option(
    "--seed",
    type=int,
    help="seed the search with this, to reproduce a run; a random one is chosen and printed otherwise",
)
@click.option(
    "--timeout",
    type=float,
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="per-document timeout in seconds",
)
@click.option(
    "--shrink/--no-shrink",
    default=True,
    show_default=True,
    help="shrink each disagreement to the simplest document that still shows it",
)
@click.option(
    "--keep",
    type=click.Path(file_okay=False, path_type=Path),
    help="write each disagreement's input into this directory",
)
@click.pass_context
def fuzz(ctx: click.Context, /, **kwargs: Any) -> None:
    """Run every implementation over generated documents, and report where they disagree."""
    selection = Selection.take(kwargs)
    invoke(ctx, lambda: run(selection, Options(**kwargs), sys.stdout))
