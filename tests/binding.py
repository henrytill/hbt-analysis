"""The contract between a Click command and the record its callback builds.

Click binds by *name*: it derives a keyword argument from each option's
declarations and calls the callback with them, never reading the callback's
signature. So a renamed option and a stale field typecheck, lint clean, and
fail at the first invocation. Every command in the package passes its options
through a dataclass, so every command gets these checks.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

import click


def assert_every_option_names_a_field(test: unittest.TestCase, command: click.Command, options: Any) -> None:
    """A renamed flag would otherwise fail only when the command is run."""
    exposed = {p.name for p in command.params if p.expose_value}
    test.assertEqual(exposed, {f.name for f in dataclasses.fields(options)})


def assert_the_defaults_agree(test: unittest.TestCase, command: click.Command, options: Any) -> None:
    """Two copies of a default is one that can drift.

    Click leaves an unstated default as a sentinel and passes None for it, so
    only the ones actually spelled out on both sides compare -- which is every
    option carrying a number.
    """
    fields = {f.name: f.default for f in dataclasses.fields(options) if f.default is not dataclasses.MISSING}
    compared = 0
    for param in command.params:
        if not isinstance(param, click.Option) or param.is_flag or param.name not in fields:
            continue
        if isinstance(param.default, (int, float, str)):
            test.assertEqual(param.default, fields[param.name], param.name)
            compared += 1
    test.assertTrue(compared, "no default was actually compared")
