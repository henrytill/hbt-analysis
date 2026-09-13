"""Command line interface."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import click

from hbt import bench
from hbt.bench import core, report


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    """Everything a run is told.

    A record rather than the parser's own namespace, so `run` is callable in
    process -- by a test, or by the conformance matrix that will want to
    benchmark and check the same four binaries -- without either of them
    reaching for a command-line parser to build an argument list.
    """

    corpus: Path | None = None
    output: Path | None = None
    report: Path | None = None
    report_only: Path | None = None
    build: bool = False
    impl: tuple[str, ...] = field(default_factory=tuple)
    binary: tuple[str, ...] = field(default_factory=tuple)
    revision: tuple[str, ...] = field(default_factory=tuple)
    info_only: bool = False
    warmup: int = 20
    min_runs: int | None = None


def parse_pairs(specs: tuple[str, ...], flag: str, shape: str, known: list[str]) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments, validating NAME."""
    parsed: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or name not in known:
            raise core.BenchmarkError(f"{flag} expects {shape} with a known NAME, got {spec!r}")
        parsed[name] = value
    return parsed


def choose(
    root: Path, impl: tuple[str, ...], binary: tuple[str, ...], revision: tuple[str, ...]
) -> tuple[list[str], list[str], dict[str, Path], dict[str, str]]:
    """The known implementations, the ones a run names, and its overrides, validated.

    Shared with hbt-matrix, which takes the same three flags so that the
    flake's wrapper can hand both commands the same arguments.
    """
    known = core.implementations(root)
    names = list(impl or known)
    unknown = set(names) - set(known)
    if unknown:
        raise core.BenchmarkError(f"unknown implementation(s): {', '.join(sorted(unknown))}")
    overrides = {n: Path(v) for n, v in parse_pairs(binary, "--binary", "NAME=PATH", known).items()}
    revisions = parse_pairs(revision, "--revision", "NAME=REV", known)
    return known, names, overrides, revisions


def write(path: Path, text: str) -> None:
    """Write a file this run produced, and say where it went."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)


def write_report(data: dict[str, Any], path: Path | None) -> None:
    """Render `data` and write it, defaulting to stdout when no path is given."""
    text = report.render(data)
    if path is None:
        sys.stdout.write(text)
        return
    write(path, text)


def run(options: Options) -> int:
    """Run the benchmark (or just re-render) and write the outputs."""
    if options.report_only:
        # Deliberately before repo_root(): re-rendering must work outside a git
        # checkout, because the Nix derivation that builds the published page
        # does exactly that in the sandbox.
        write_report(core.load_results(options.report_only), options.report)
        return 0

    root = core.repo_root()
    bench_dir = root / "benchmarks"
    published = bench_dir / "results.json"
    output = options.output or published

    # A timing-less document must never land on the file the page is built from:
    # --info-only has no timings by construction, and .#site renders whatever
    # benchmarks/results.json holds. Compared as resolved paths rather than by
    # "was -o given", so naming the file explicitly is refused too -- which file
    # gets written is the invariant, not how the caller chose it.
    if options.info_only and output.resolve() == published.resolve():
        raise core.BenchmarkError(f"--info-only writes no timings; -o must not be {published}")

    _, names, overrides, revisions = choose(root, options.impl, options.binary, options.revision)
    # --build refreshes the result-* symlinks, which an override bypasses.
    if options.build:
        core.build(root, [n for n in names if n not in overrides])
    impls = core.discover(root, names, overrides, revisions)
    if not any(i.available for i in impls):
        raise core.BenchmarkError("no built implementations found; try --build")
    inputs = core.load_corpus(root, options.corpus or bench_dir / "corpus.toml")
    pairs = core.verify(impls, inputs)
    hyperfine = None if options.info_only else core.benchmark(pairs, inputs, options.warmup, options.min_runs)
    data = core.collect(impls, inputs, pairs, hyperfine)

    # A corpus none of whose inputs exist still produces a document, and that
    # document is what the Pages workflow publishes. Refuse to write one rather
    # than let a fresh checkout quietly overwrite real numbers with a grid of
    # errors. Under --info-only the timings are absent by construction, so the
    # entity counts are what has to be there instead.
    if options.info_only:
        if not any(p.ok for p in pairs):
            raise core.BenchmarkError("nothing was parsed; check the corpus paths")
    elif not any(p.timing for p in pairs):
        raise core.BenchmarkError("nothing was benchmarked; check the corpus paths")

    write(output, core.dump_results(data))

    # No default path: results.json is the only file this leaves in the tree.
    # Markdown and HTML are translations of it -- report.md goes to stdout
    # unless asked for, and the HTML is produced by `nix build .#site`.
    write_report(data, options.report)
    return 0


def invoke(ctx: click.Context, prog: str, command: Callable[[], int]) -> None:
    """Run `command` and exit with its status, turning the expected failures into one.

    Shared by every command in the package, so each exit status means the
    same thing whichever of them returned it.
    """
    try:
        ctx.exit(command())
    except core.BenchmarkError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        ctx.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        ctx.exit(1)
    except KeyboardInterrupt:
        # Click's own handler would abort with 1; a benchmark is long enough
        # that being interrupted is ordinary, and 130 says which signal did it.
        ctx.exit(130)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--corpus",
    type=click.Path(path_type=Path),
    help="corpus TOML (default: benchmarks/corpus.toml)",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="results JSON (default: benchmarks/results.json)",
)
@click.option(
    "-r",
    "--report",
    type=click.Path(path_type=Path),
    help="write the Markdown report here (default: stdout)",
)
@click.option(
    "--report-only",
    # Deliberately not `exists=True`: `core.load_results` diagnoses a missing
    # or malformed results file, and that one message is what `nix build
    # .#site` should fail with.  Two checks would mean two wordings for the
    # same mistake, and would leave the one core.py documents unreachable.
    type=click.Path(dir_okay=False, path_type=Path),
    metavar="RESULTS",
    help="re-render a saved results file",
)
@click.option(
    "--build",
    is_flag=True,
    help="nix build each implementation first",
)
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
    "--info-only",
    is_flag=True,
    help="run the --info stage and skip the timings (requires -o)",
)
@click.option(
    "--warmup",
    type=click.IntRange(min=0),
    default=20,
    show_default=True,
    help="hyperfine warmup runs",
)
@click.option(
    "--min-runs",
    type=click.IntRange(min=1),
    help="hyperfine minimum runs (default: hyperfine's own)",
)
@click.version_option(bench.__version__, "--version", prog_name="hbt-bench")
@click.pass_context
def cli(ctx: click.Context, /, **kwargs: Any) -> None:
    """Parse the command line, run it, and turn the expected failures into a status.

    The help Click prints is set below, from the package docstring; this one
    describes the function.
    """
    invoke(ctx, "hbt-bench", lambda: run(Options(**kwargs)))


# pyproject declares the package docstring as the distribution summary, so it
# is already the one-line description of this tool. Assigned rather than
# repeated in the docstring above, where a second copy could drift from it.
cli.help = bench.__doc__
