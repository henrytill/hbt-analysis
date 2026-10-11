"""The contract between a Click command and the records its callback builds.

Click binds by *name*: it derives a keyword argument from each option's
declarations and calls the callback with them, never reading the callback's
signature. So a renamed option and a stale field typecheck, lint clean, and
fail at the first invocation. Every command in the package passes its options
through dataclasses -- its own Options, and the Selection they all share -- so
every command gets these checks.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

import click


def _fields(records: tuple[Any, ...]) -> list[dataclasses.Field[Any]]:
    return [field for record in records for field in dataclasses.fields(record)]


def assert_every_option_names_a_field(test: unittest.TestCase, command: click.Command, *records: Any) -> None:
    """A renamed flag would otherwise fail only when the command is run.

    The fields are pooled across `records`, and a name in two of them is a
    failure too: a command's callback splits its arguments between the
    records, so only one of them would receive it.
    """
    names = [field.name for field in _fields(records)]
    test.assertEqual(len(names), len(set(names)), "a field is declared by two records")
    exposed = {p.name for p in command.params if p.expose_value}
    test.assertEqual(exposed, set(names))


def assert_the_defaults_agree(test: unittest.TestCase, command: click.Command, *records: Any) -> None:
    """Two copies of a default is one that can drift.

    Compared as the arguments Click builds from an empty command line, not as
    each option's `default`: that is what the callback actually receives, and
    it covers what `default` leaves unstated -- the False of a flag, the empty
    tuple of a repeatable option, the None of an optional one.
    """
    given = command.make_context(command.name or "", []).params
    defaults = {f.name: f.default for f in _fields(records)}
    test.assertEqual(given, defaults)
