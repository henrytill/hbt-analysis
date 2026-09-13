"""The commands, and what every one of them shares.

Every command runs over the same implementations, chosen the same way --
`--impl` limits the set, `--binary` overrides where one comes from,
`--revision` records which build it is -- and exits with the same statuses.  So
both are stated here once, and each command's own record keeps only what that
command alone is told.  The implementations themselves are
:mod:`hbt.analysis.implementations`; this is only the Click side.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable, TypeVar

import click

from hbt.analysis.implementations import ImplementationError
from hbt.bench import BenchmarkError
from hbt.conformance import CorpusError

F = TypeVar("F", bound=Callable[..., Any])


class CommandError(Exception):
    """A condition that should stop a command with a message, not a traceback.

    Raised by a command for a refusal of its own; :func:`invoke` turns it into
    exit status 2 for every command.
    """


def selection_options(command: F) -> F:
    """Give `command` the options a :class:`hbt.analysis.implementations.Selection` is built from.

    On each command rather than on the `hbt-analysis` group, so that they
    follow the command name like every other option.  A group option has to
    come before it, and the flake's apps put the command name first, so
    `nix run .#bench -- --impl hbt-rs` would stop working.
    """
    options = (
        click.option(
            "--impl",
            multiple=True,
            metavar="NAME",
            help="limit to this implementation (repeatable)",
        ),
        click.option(
            "--binary",
            multiple=True,
            metavar="NAME=PATH",
            help="use this binary for NAME instead of result-NAME/bin/hbt (repeatable)",
        ),
        click.option(
            "--revision",
            multiple=True,
            metavar="NAME=REV",
            help="record REV as the revision NAME's binary was built from (repeatable)",
        ),
    )
    # Applied last to first, which is the order stacked decorators apply in,
    # so the help lists them as written here.
    for option in reversed(options):
        command = option(command)
    return command


def invoke(ctx: click.Context, command: Callable[[], int]) -> None:
    """Run `command` and exit with its status, turning the expected failures into one.

    Shared by every command, so each exit status means the same thing
    whichever of them returned it.  A message is prefixed with the command's
    full path, `hbt-analysis bench`, so it says which command refused.

    The libraries' own errors are refusals here too, so no command has to
    translate the error of the library it drives into one of these.
    """
    try:
        ctx.exit(command())
    except (CommandError, ImplementationError, BenchmarkError, CorpusError) as exc:
        print(f"{ctx.command_path}: {exc}", file=sys.stderr)
        ctx.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"{ctx.command_path}: {exc}", file=sys.stderr)
        ctx.exit(1)
    except KeyboardInterrupt:
        # Click's own handler would abort with 1; a benchmark is long enough
        # that being interrupted is ordinary, and 130 says which signal did it.
        ctx.exit(130)
