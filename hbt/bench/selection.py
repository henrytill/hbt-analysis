"""Which implementations a command runs over, and how a command exits.

Every command in the package runs over the same implementations, chosen the
same way -- `--impl` limits the set, `--binary` overrides where one comes from,
`--revision` records which build it is -- and exits with the same statuses.  So
both are stated here once, and each command's own record keeps only what that
command alone is told.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import click

from hbt.bench import core

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Selection:
    """The implementations a run is about, as the command line named them.

    A record of its own rather than fields repeated in each command's options,
    so that a command's `run` takes the same thing whichever command it is,
    and the flake's apps can hand every command the same arguments.
    """

    impl: tuple[str, ...] = ()
    binary: tuple[str, ...] = ()
    revision: tuple[str, ...] = ()

    @classmethod
    def take(cls, kwargs: dict[str, Any]) -> Selection:
        """Build one from a command's keyword arguments, removing the fields it used."""
        return cls(**{f.name: kwargs.pop(f.name) for f in dataclasses.fields(cls)})


def selection_options(command: F) -> F:
    """Give `command` the options a :class:`Selection` is built from.

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


def parse_pairs(specs: tuple[str, ...], flag: str, shape: str, known: list[str]) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments, validating NAME."""
    parsed: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or name not in known:
            raise core.CommandError(f"{flag} expects {shape} with a known NAME, got {spec!r}")
        parsed[name] = value
    return parsed


def choose(root: Path, selection: Selection) -> tuple[list[str], list[str], dict[str, Path], dict[str, str]]:
    """The known implementations, the ones a run names, and its overrides, validated."""
    known = core.implementations(root)
    names = list(selection.impl or known)
    unknown = set(names) - set(known)
    if unknown:
        raise core.CommandError(f"unknown implementation(s): {', '.join(sorted(unknown))}")
    overrides = {n: Path(v) for n, v in parse_pairs(selection.binary, "--binary", "NAME=PATH", known).items()}
    revisions = parse_pairs(selection.revision, "--revision", "NAME=REV", known)
    return known, names, overrides, revisions


def invoke(ctx: click.Context, command: Callable[[], int]) -> None:
    """Run `command` and exit with its status, turning the expected failures into one.

    Shared by every command in the package, so each exit status means the
    same thing whichever of them returned it.  A message is prefixed with the
    command's full path, `hbt-analysis bench`, so it says which command
    refused.
    """
    try:
        ctx.exit(command())
    except core.CommandError as exc:
        print(f"{ctx.command_path}: {exc}", file=sys.stderr)
        ctx.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"{ctx.command_path}: {exc}", file=sys.stderr)
        ctx.exit(1)
    except KeyboardInterrupt:
        # Click's own handler would abort with 1; a benchmark is long enough
        # that being interrupted is ordinary, and 130 says which signal did it.
        ctx.exit(130)
