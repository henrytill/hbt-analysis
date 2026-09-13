"""The `bench` command: time every implementation over a corpus, and report it."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from hbt.bench import core, report
from hbt.bench.selection import Selection, choose, invoke, selection_options


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    """Everything `bench` alone is told.

    A record rather than the parser's own namespace, so `run` is callable in
    process -- by a test, or by anything else driving the benchmark -- without
    reaching for a command-line parser to build an argument list.  Which
    implementations it runs over is a :class:`Selection`, the same record every
    command takes.
    """

    corpus: Path | None = None
    output: Path | None = None
    report: Path | None = None
    report_only: Path | None = None
    build: bool = False
    info_only: bool = False
    warmup: int = 20
    min_runs: int | None = None


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


def run(selection: Selection, options: Options) -> int:
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
        raise core.CommandError(f"--info-only writes no timings; -o must not be {published}")

    _, names, overrides, revisions = choose(root, selection)
    # --build refreshes the result-* symlinks, which an override bypasses.
    if options.build:
        core.build(root, [n for n in names if n not in overrides])
    impls = core.discover(root, names, overrides, revisions)
    if not any(i.available for i in impls):
        raise core.CommandError("no built implementations found; try --build")
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
            raise core.CommandError("nothing was parsed; check the corpus paths")
    elif not any(p.timing for p in pairs):
        raise core.CommandError("nothing was benchmarked; check the corpus paths")

    write(output, core.dump_results(data))

    # No default path: results.json is the only file this leaves in the tree.
    # Markdown and HTML are translations of it -- report.md goes to stdout
    # unless asked for, and the HTML is produced by `nix build .#site`.
    write_report(data, options.report)
    return 0


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@selection_options
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
@click.pass_context
def bench(ctx: click.Context, /, **kwargs: Any) -> None:
    """Time every implementation over a corpus, and render the report."""
    selection = Selection.take(kwargs)
    invoke(ctx, lambda: run(selection, Options(**kwargs)))
