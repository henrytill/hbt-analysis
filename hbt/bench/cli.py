"""The `hbt-analysis` command line.

One executable with a command per job, because every job here runs over the
same implementations: `bench` times them and `conformance` holds them to the
corpus.  Each command lives in a module of its own, with a record of its options
and a `run` callable in process; this module only gathers them.
"""

from __future__ import annotations

import click

from hbt import bench
from hbt.bench import benchmark, matrix


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(bench.__version__, "--version", prog_name="hbt-analysis")
def cli() -> None:
    """Dispatch to a command.

    The help Click prints is set below, from the package docstring; this one
    describes the function.
    """


cli.add_command(benchmark.bench)
cli.add_command(matrix.conformance)

# pyproject declares the package docstring as the distribution summary, so it
# is already the one-line description of this tool. Assigned rather than
# repeated in the docstring above, where a second copy could drift from it.
cli.help = bench.__doc__
