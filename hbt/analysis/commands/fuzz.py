"""The `fuzz` command: hold every implementation to the others on generated documents.

The generating, judging and shrinking are :mod:`hbt.fuzz`; this finds the
implementations, hands their binaries over, chooses the seed, and decides what
a run prints and writes.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import click

from hbt.analysis.commands import CommandError, invoke, print_table, selection_options
from hbt.analysis.implementations import Impl, Selection, choose, discover, repo_root
from hbt.fuzz import GENERATORS, Disagreement, Trial, report, search

# A fuzzed document is a few dozen lines; anything slower than this is a hang.
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class Options:
    """Everything `fuzz` alone is told; which implementations is a :class:`Selection`."""

    format: str = "markdown"
    examples: int = 200
    seed: int | None = None
    timeout: float = DEFAULT_TIMEOUT
    shrink: bool = True
    keep: Path | None = None


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


def _keep(directory: Path, stem: str, found: list[Disagreement], suffix: str) -> None:
    """Write each disagreement's input where it can be turned into a fixture."""
    directory.mkdir(parents=True, exist_ok=True)
    for index, disagreement in enumerate(found, start=1):
        (directory / f"{stem}-{index}.input{suffix}").write_text(disagreement.text, encoding="utf-8")


def run(selection: Selection, options: Options, out: TextIO) -> int:
    """Fuzz the selected implementations; 0 if they agreed on every document."""
    root = repo_root()
    impls = discover(root, *choose(root, selection)[1:])
    binaries = {impl.name: impl.binary for impl in impls if impl.binary is not None}
    if len(binaries) < 2:
        raise CommandError("fuzzing needs at least two implementations with a binary")
    generator = GENERATORS[options.format]
    seed_value = options.seed if options.seed is not None else int.from_bytes(os.urandom(4))
    _header(seed_value, options, impls, out)

    with tempfile.TemporaryDirectory(prefix="hbt-fuzz-") as scratch:
        trial = Trial(binaries, generator.suffix, options.timeout, Path(scratch))
        try:
            found = search(
                trial, generator.strategy, examples=options.examples, shrink=options.shrink, seed_value=seed_value
            )
        finally:
            trial.pool.shutdown()

    for index, disagreement in enumerate(found, start=1):
        report(index, disagreement, out)
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
