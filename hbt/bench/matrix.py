"""Holding every implementation to the corpus at once.

`hbt.conformance` checks one executable; this checks all of them and lays the
results side by side, so a fixture that one implementation fails and three pass
reads as one row rather than as four logs to compare.

Each implementation is checked against the corpus *it* pins, found through its
own .gitmodules, rather than against one corpus chosen here.  The pins differ by
design -- each implementation's CI runs its own revision -- and unifying them
would hide the thing the header reports: which revision each column is a
statement about.  `--corpus` names one directory for all of them instead, which
is how to ask whether everyone passes the corpus being edited.

Nothing here decides what conformance means.  Discovery, comparison and waivers
are all `hbt.conformance`'s, imported rather than reimplemented, so this and an
implementation's own check cannot disagree about a fixture.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence, TextIO

import click

from hbt import bench
from hbt.bench import core
from hbt.bench.cli import choose, parse_pairs
from hbt.conformance import __version__ as harness_version
from hbt.conformance.cli import read_waivers
from hbt.conformance.corpus import Corpus, CorpusError, Fixture, revision
from hbt.conformance.runner import DEFAULT_TIMEOUT, Outcome, Result, check_all

# The repository an implementation's corpus submodule points at, by name.
CORPUS_REPOSITORY = "hbt-data"

# A cell with no result: the fixture is not in that implementation's corpus, or
# the implementation could not be run.  One field wide, so every row of the
# matrix has the same field count for awk.
ABSENT = "-"


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    """Everything a run is told."""

    impl: tuple[str, ...] = field(default_factory=tuple)
    binary: tuple[str, ...] = field(default_factory=tuple)
    revision: tuple[str, ...] = field(default_factory=tuple)
    waivers: tuple[str, ...] = field(default_factory=tuple)
    corpus: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    tz: str | None = None
    jobs: int = 8
    list_only: bool = False
    quiet: bool = False
    patterns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Column:
    """One implementation's results, or the reason it has none."""

    impl: core.Impl
    corpus: Corpus | None = None
    results: dict[str, Result] = field(default_factory=dict[str, Result])
    stale: tuple[str, ...] = ()
    error: str | None = None
    """Why there is no corpus; a missing binary is already the impl's error."""

    @property
    def unavailable(self) -> str | None:
        """Why this implementation could not be checked, if it could not."""
        return self.error or self.impl.error

    @property
    def ok(self) -> bool:
        """Whether this implementation conformed on everything it was given."""
        return self.unavailable is None and not self.stale and all(r.outcome.ok for r in self.results.values())

    def cell(self, name: str) -> str:
        """What this column says about fixture `name`."""
        result = self.results.get(name)
        return ABSENT if result is None else result.outcome.value.upper()


def _is_corpus(url: str) -> bool:
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1] == CORPUS_REPOSITORY


def corpus_root(root: Path, name: str) -> Path:
    """Where implementation `name` checks out the corpus it pins.

    Read from its .gitmodules, by the URL, rather than from a table of paths:
    the four mount the corpus at four different paths, and hbt-data#14 moves
    each to `hbt-data/` in its own time, so any path written here would be
    wrong for some of them throughout that migration.

    Raises `CorpusError` with the reason when there is no single such
    submodule, or it is not checked out.
    """
    gitmodules = root / name / ".gitmodules"
    try:
        paths = sorted(path for path, url in core.submodules(gitmodules).items() if _is_corpus(url))
    except core.BenchmarkError as exc:
        raise CorpusError(str(exc)) from exc
    if len(paths) != 1:
        found = "none" if not paths else ", ".join(paths)
        raise CorpusError(f"{gitmodules}: expected one {CORPUS_REPOSITORY} submodule, found {found}")
    path = root / name / paths[0]
    if not path.is_dir() or not any(path.iterdir()):
        raise CorpusError(f"{path} is not checked out (git submodule update --init --recursive)")
    return path


def _discover(path: Path) -> Corpus:
    """The corpus at `path`, refusing one with no fixtures as hbt-conformance does.

    A column over zero fixtures has nothing that can fail, so it would pass.
    """
    corpus = Corpus.discover(path)
    if not corpus.fixtures:
        raise CorpusError(f"no fixtures under {corpus.root} -- is that a corpus checkout?")
    return corpus


def locate(root: Path, names: Sequence[str], override: Path | None) -> dict[str, Corpus | str]:
    """Each implementation's corpus, or why it has none.

    A corpus that cannot be found is one implementation's problem and becomes
    its column's error.  An override is the caller's, so it stops the run.
    """
    if override is not None:
        try:
            shared = _discover(override)
        except CorpusError as exc:
            raise core.BenchmarkError(str(exc)) from exc
        return dict.fromkeys(names, shared)
    corpora: dict[str, Corpus | str] = {}
    for name in names:
        try:
            corpora[name] = _discover(corpus_root(root, name))
        except CorpusError as exc:
            corpora[name] = str(exc)
    return corpora


def check_column(
    impl: core.Impl, corpus: Corpus | str, selected: Sequence[Fixture], waivers: Path | None, options: Options
) -> Column:
    """Run one implementation over its selected fixtures."""
    if isinstance(corpus, str):
        return Column(impl, error=corpus)
    if impl.binary is None:
        return Column(impl, corpus)
    waived = read_waivers(waivers) if waivers else {}
    results = check_all(selected, impl.binary, options.timeout, options.tz, options.jobs, waived)
    stale = tuple(sorted(set(waived) - {f.name for f in corpus.fixtures}))
    return Column(impl, corpus, {r.fixture.name: r for r in results}, stale)


def _widths(rows: Sequence[Sequence[str]]) -> list[int]:
    return [max(map(len, column)) for column in zip(*rows)]


def _line(cells: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()


def _print_table(rows: Sequence[Sequence[str]], out: TextIO) -> None:
    widths = _widths(rows)
    for row in rows:
        print(_line(row, widths), file=out)


def _header(columns: Sequence[Column], tz: str | None, out: TextIO) -> None:
    """What ran, against which corpus, one implementation to a line.

    The corpus revision is per implementation because it is pinned per
    implementation; when they differ, a disagreement in the matrix may be a
    disagreement about which fixtures exist, and the note says so.  The build
    is last because an implementation's `--version` string holds spaces, and a
    known revision is shortened to match the corpus column beside it.
    """
    print(f"harness  hbt.conformance {harness_version}", file=out)
    if tz is not None:
        print(f"TZ       {tz}", file=out)
    print(file=out)
    rows = [("", "corpus", "fixtures", "binary", "build")]
    revisions = [ABSENT if c.corpus is None else revision(c.corpus.root) for c in columns]
    for c, rev in zip(columns, revisions):
        fixtures = ABSENT if c.corpus is None else f"{len(c.results)} of {len(c.corpus.fixtures)}"
        build = (c.impl.revision or "")[:7] or c.impl.version or ABSENT
        rows.append((c.impl.name, rev, fixtures, str(c.impl.binary or ABSENT), build))
    _print_table(rows, out)
    pinned = set(revisions) - {ABSENT}
    if len(pinned) > 1:
        print(f"\nnote: {len(pinned)} different corpus revisions are pinned", file=out)


def _matrix(columns: Sequence[Column], names: Sequence[str], quiet: bool, out: TextIO) -> None:
    """One row per fixture, with what went wrong beneath it.

    `--quiet` drops the rows every implementation that has the fixture passed.
    An absent cell does not count against a row: it says which corpus a
    column ran, which the header already reports.
    """
    rows = [(name, [c.cell(name) for c in columns]) for name in names]
    shown = [(name, cells) for name, cells in rows if not (quiet and all(x in ("PASS", ABSENT) for x in cells))]
    if not shown:
        return
    header = ["", *(c.impl.name for c in columns)]
    widths = _widths([header, *([name, *cells] for name, cells in shown)])
    print(file=out)
    print(_line(header, widths), file=out)
    for name, cells in shown:
        print(_line([name, *cells], widths), file=out)
        for c in columns:
            result = c.results.get(name)
            if result is None or result.outcome is Outcome.PASS:
                continue
            print(f"    {c.impl.name}: {result.reason}", file=out)
            for difference in result.differences:
                for text in difference.render().splitlines():
                    print(f"        {text}", file=out)


def _totals(columns: Sequence[Column], out: TextIO) -> None:
    rows: list[tuple[str, str]] = []
    for c in columns:
        if c.unavailable is not None:
            rows.append((c.impl.name, f"unavailable: {c.unavailable}"))
            continue
        counts = Counter(r.outcome for r in c.results.values())
        rows.append((c.impl.name, ", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o]) or "nothing ran"))
    print(file=out)
    _print_table(rows, out)
    for c in columns:
        for name in c.stale:
            print(f"warning: {c.impl.name} waives unknown fixture {name}", file=out)


def _waiver_files(specs: tuple[str, ...], known: list[str]) -> dict[str, Path]:
    """Each implementation's waivers file, checked to exist before anything runs."""
    files = {n: Path(v) for n, v in parse_pairs(specs, "--waivers", "NAME=FILE", known).items()}
    for path in files.values():
        if not path.is_file():
            raise core.BenchmarkError(f"--waivers: no such file {path}")
    return files


def _list(corpora: Mapping[str, Corpus | str], fixtures: Sequence[str], out: TextIO) -> None:
    """The fixtures a run would check, with any corpus that could not be found on stderr."""
    for name, corpus in corpora.items():
        if isinstance(corpus, str):
            print(f"{name}: {corpus}", file=sys.stderr)
    for fixture in fixtures:
        print(fixture, file=out)


def run(options: Options, out: TextIO) -> int:
    """Check the selected implementations; 0 if every one of them conformed."""
    root = core.repo_root()
    known, names, overrides, revisions = choose(root, options.impl, options.binary, options.revision)
    waivers = _waiver_files(options.waivers, known)

    corpora = locate(root, names, options.corpus)
    patterns = list(options.patterns)
    selected = {name: c.select(patterns) for name, c in corpora.items() if isinstance(c, Corpus)}
    fixtures = sorted({f.name for chosen in selected.values() for f in chosen})

    if options.list_only:
        _list(corpora, fixtures, out)
        return 0

    # A corpus is refused if it has no fixtures, so with one selected an empty
    # list can only mean the filters matched nothing.
    if not selected:
        raise core.BenchmarkError("no implementation has a corpus to check")
    if not fixtures:
        raise core.BenchmarkError(f"no fixture matches {' '.join(patterns)}")

    impls = core.discover(root, names, overrides, revisions)
    columns = [
        check_column(impl, corpora[impl.name], selected.get(impl.name, []), waivers.get(impl.name), options)
        for impl in impls
    ]
    _header(columns, options.tz, out)
    _matrix(columns, fixtures, options.quiet, out)
    _totals(columns, out)
    return 0 if all(c.ok for c in columns) else 1


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--impl",
    multiple=True,
    metavar="NAME",
    help="limit to this implementation (repeatable)",
)
@click.option(
    "--binary",
    multiple=True,
    metavar="NAME=PATH",
    help="use this binary for NAME instead of result-NAME/bin/hbt (repeatable)",
)
@click.option(
    "--revision",
    multiple=True,
    metavar="NAME=REV",
    help="record REV as the revision NAME's binary was built from (repeatable)",
)
@click.option(
    "--waivers",
    multiple=True,
    metavar="NAME=FILE",
    help="fixtures NAME is expected to fail, one per line (repeatable)",
)
@click.option(
    "--corpus",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="check every implementation against this corpus instead of the one it pins",
)
@click.option(
    "--timeout",
    type=float,
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="per-fixture timeout in seconds",
)
@click.option(
    "--tz",
    help="run under this timezone instead of the ambient one",
)
@click.option(
    "-j",
    "--jobs",
    type=click.IntRange(min=1),
    default=8,
    show_default=True,
    help="fixtures to run at once, per implementation",
)
@click.option(
    "-l",
    "--list",
    "list_only",
    is_flag=True,
    help="list the selected fixtures and exit",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="show only fixtures some implementation did not pass",
)
@click.version_option(bench.__version__, "--version", prog_name="hbt-matrix")
@click.argument("patterns", nargs=-1, metavar="[FILTER]...")
@click.pass_context
def cli(ctx: click.Context, /, **kwargs: object) -> None:
    """Check every hbt implementation against the corpus, side by side.

    FILTER selects fixtures by name, substring or glob; every fixture runs if
    none is given.
    """
    try:
        ctx.exit(run(Options(**kwargs), sys.stdout))  # type: ignore[arg-type]
    except core.BenchmarkError as exc:
        print(f"hbt-matrix: {exc}", file=sys.stderr)
        ctx.exit(2)
    except KeyboardInterrupt:
        ctx.exit(130)


# Guarded for the same reason hbt/bench/__main__.py is.
if __name__ == "__main__":
    cli(prog_name="hbt-matrix")  # pylint: disable=no-value-for-parameter
