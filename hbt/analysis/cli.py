"""The `hbt-analysis` command line.

One executable with a command per job, because every job here runs over the
same implementations: `bench` times them, `conformance` holds them to the
corpus, and `fuzz` holds them to each other.  Each command lives in a module of
its own under :mod:`hbt.analysis.commands`, with a record of its options and a
`run` callable in process; this module only gathers them.
"""

from __future__ import annotations

import click

from hbt import analysis
from hbt.analysis.commands import bench, conformance, fuzz


@click.group(help=analysis.__doc__, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(analysis.__version__, "--version", prog_name="hbt-analysis")
def cli() -> None:
    """Dispatch to a command; the help Click prints is the package docstring."""


cli.add_command(bench.bench)
cli.add_command(conformance.conformance)
cli.add_command(fuzz.fuzz)
