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
simplest document that shows it.  "Shows it" has to mean *the same* atom:
deleting a date heading turns most disagreements into the one about undated
links, and a shrunk input would then show a different bug from the one found.
So each atom is raised as an exception type of its own, and Hypothesis, which
tells bugs apart by the type of what they raise, keeps them apart while it
shrinks and reports each one.

Hypothesis is built to fail a test, not to survey: once it has a bug it goes on
generating for ten seconds at most, and less if the latter half of that finds
nothing new.  So the search runs in rounds.  Each round passes the atoms found
so far, which sends Hypothesis after a new one, and the search ends with the
first round that gets through its examples without one.
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
from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
from hypothesis.errors import FlakyFailure

from hbt.analysis.commands import CommandError, invoke, selection_options
from hbt.analysis.generators import GENERATORS, Generator
from hbt.analysis.implementations import Impl, Selection, choose, discover, repo_root
from hbt.conformance.runner import FORMATS

# A fuzzed document is a few dozen lines; anything slower than this is a hang.
DEFAULT_TIMEOUT = 10.0

# Differences shown per pair of classes. The first few say what kind of
# disagreement it is; the input says the rest.
SHOWN = 5

# Lines of a failure's stderr shown. The first few name the error; a backtrace
# after them says nothing a report reader needs.
STDERR = 4

# The input's path as it appears in a diagnostic.
INPUT = "<input>"


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

    `detail` is everything it wrote to stderr, with the input's path replaced,
    so that two failures compare equal when they are the same failure of the
    same document wherever it was written.  All of it rather than one line:
    which line says why differs by implementation, and an environment with
    RUST_BACKTRACE set puts a stack trace after hbt-rs's message.
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


def execute(binary: Path, path: Path, timeout: float) -> Verdict:
    """Run one implementation over one document, the way the harness runs it over a fixture."""
    try:
        proc = subprocess.run([str(binary), "-t", "yaml", str(path)], capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Failure(f"timed out after {timeout:g}s")
    except OSError as exc:
        return Failure(f"could not run {binary}: {exc}")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").replace(str(path), INPUT)
        return Failure(f"exit {proc.returncode}", tuple(line.rstrip() for line in stderr.strip().splitlines()))
    try:
        document: dict[str, Any] = FORMATS["yaml"].parse(proc.stdout)
    # Whatever an implementation wrote that does not read as a Collection is its
    # failure, not the fuzzer's: a YAML error, a shape the harness refuses, and
    # PyYAML's own recursion limit on pathological output alike.
    except Exception as exc:  # pylint: disable=broad-exception-caught
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
    """The implementations grouped by agreement: failures together, Collections by having no difference."""

    def agree(a: str, b: str) -> bool:
        x, y = verdicts[a], verdicts[b]
        if isinstance(x, Failure) or isinstance(y, Failure):
            return isinstance(x, Failure) and isinstance(y, Failure)
        return not FORMATS["yaml"].diff(x, y)

    return group(verdicts, agree)


class Disagreement(Exception):
    """A document showing an atom not found before.

    Raised from inside the Hypothesis test, never subclassed by hand: each atom
    gets a subclass of its own, made by :class:`Trial`, which is what keeps
    Hypothesis from shrinking one disagreement into another.
    """

    def __init__(self, text: str, verdicts: dict[str, Verdict], atom: Atom) -> None:
        super().__init__(atom.describe())
        self.text = text
        self.verdicts = verdicts
        self.atom = atom


class Trial:
    """Runs the implementations over documents, and raises what they disagree on."""

    def __init__(self, impls: list[Impl], generator: Generator, timeout: float, scratch: Path) -> None:
        self.binaries = {impl.name: impl.binary for impl in impls if impl.binary is not None}
        self.generator = generator
        self.timeout = timeout
        self.scratch = scratch
        self.pool = ThreadPoolExecutor(max_workers=len(self.binaries))
        # In the order found, which is the order they are reported in.
        self.kinds: dict[Atom, type[Disagreement]] = {}
        # Found in an earlier round, and so passed from now on.
        self.known: set[Atom] = set()

    def judge(self, text: str) -> dict[str, Verdict]:
        """What each implementation makes of `text`, run side by side."""
        fd, name = tempfile.mkstemp(suffix=self.generator.suffix, dir=self.scratch)
        path = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            futures = {impl: self.pool.submit(execute, b, path, self.timeout) for impl, b in self.binaries.items()}
            return {impl: future.result() for impl, future in futures.items()}
        finally:
            path.unlink()

    def check(self, text: str) -> None:
        """Raise a :class:`Disagreement` for the least atom of this document not already known.

        The least rather than any, so that one document always raises the
        same kind, which Hypothesis's shrinking depends on.
        """
        verdicts = self.judge(text)
        new = sorted(atoms(verdicts) - self.known)
        if not new:
            return
        atom = new[0]
        if atom not in self.kinds:
            self.kinds[atom] = type(f"Disagreement{len(self.kinds) + 1}", (Disagreement,), {})
        raise self.kinds[atom](text, verdicts, atom)


def search(trial: Trial, options: Options, seed_value: int) -> list[Disagreement]:
    """Every atom found, each with its shrunk document, round by round."""
    found: list[Disagreement] = []
    for number in itertools.count():
        new = search_round(trial, options, f"{seed_value}/{number}")
        if not new:
            return found
        found.extend(new)
        trial.known.update(d.atom for d in new)
    raise AssertionError("unreachable")


def search_round(trial: Trial, options: Options, seed_value: str) -> list[Disagreement]:
    """The atoms one Hypothesis run finds, each with its shrunk document.

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
        report_multiple_bugs=True,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(trial.generator.strategy)
    def probe(text: str) -> None:
        trial.check(text)

    try:
        # Hypothesis supplies `text`; pylint reads the undecorated signature.
        probe()  # pylint: disable=no-value-for-parameter
    except FlakyFailure as exc:
        raise CommandError(f"an implementation answered one document two ways: {exc}") from exc
    except Disagreement as exc:
        return [exc]
    except BaseExceptionGroup as exc:
        found, rest = exc.split(Disagreement)
        if rest is not None:
            raise rest from None
        return [] if found is None else _flatten(found)
    return []


def _flatten(exc: BaseExceptionGroup[Disagreement]) -> list[Disagreement]:
    found: list[Disagreement] = []
    for inner in exc.exceptions:
        found.extend(_flatten(inner) if isinstance(inner, BaseExceptionGroup) else [inner])
    return found


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
    width = max(len(impl.name) for impl in impls)
    for impl in impls:
        if impl.binary is None:
            print(f"{impl.name.ljust(width)}  unavailable: {impl.error}", file=out)
            continue
        build = (impl.revision or "")[:7] or impl.version or "-"
        print(f"{impl.name.ljust(width)}  {impl.binary}  {build}", file=out)


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
    reference = representatives[0]
    for members, verdict in zip(groups[1:], representatives[1:]):
        if isinstance(reference, Failure) or isinstance(verdict, Failure):
            continue
        print(f"  {members[0]} against {groups[0][0]}:", file=out)
        differences = FORMATS["yaml"].diff(reference, verdict)
        for difference in differences[:SHOWN]:
            for line in difference.render().splitlines():
                print(f"      {line}", file=out)
        if len(differences) > SHOWN:
            print(f"      ... and {len(differences) - SHOWN} more", file=out)
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
        trial = Trial(impls, generator, options.timeout, Path(scratch))
        try:
            found = search(trial, options, seed_value)
        finally:
            trial.pool.shutdown()

    # Reported in the order first found, which a seeded run repeats.
    order = list(trial.kinds)
    found.sort(key=lambda d: order.index(d.atom))
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
