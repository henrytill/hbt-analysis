"""Entry point."""

from __future__ import annotations

from hbt.bench.cli import cli

# Click supplies every parameter from the command line; pylint reads the
# decorated function's signature and sees them missing.
cli(prog_name="hbt-bench")  # pylint: disable=no-value-for-parameter
