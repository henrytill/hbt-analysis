"""Entry point."""

from __future__ import annotations

from hbt.bench.cli import cli

# Guarded, because this module is importable under its own name: a doctest
# collector or any `walk_packages` sweep imports it, and an unguarded call
# would parse that tool's argv, run a benchmark and exit out from under it.
if __name__ == "__main__":
    # Click supplies every parameter from the command line; pylint reads the
    # decorated function's signature and sees them missing.
    cli(prog_name="hbt-bench")  # pylint: disable=no-value-for-parameter
