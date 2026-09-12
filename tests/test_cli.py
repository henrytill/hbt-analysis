"""Tests for the command line's contract with the code behind it.

Click binds by *name*: it derives a keyword argument from each option's
declarations and calls the callback with them, never reading the callback's
signature. So a renamed option and a stale parameter typecheck, lint clean,
and fail at the first invocation. These tests are what notices.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import dataclasses
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import click
from click.testing import CliRunner

from hbt.bench import core
from hbt.bench.cli import Options, cli, parse_pairs

RESULTS = {
    "version": core.FORMAT_VERSION,
    "generated": "2026-01-01T00:00:00+00:00",
    "hyperfine": None,
    "host": {"node": "somewhere", "machine": "x86_64", "system": "Linux", "release": "6"},
    "implementations": [{"name": "hbt-rs", "store_path": None, "version": None, "revision": None, "error": None}],
    "inputs": [{"name": "markdown", "path": "markdown.md"}],
    "results": [
        {"implementation": "hbt-rs", "input": "markdown", "entities": 3, "error": None, "timing": None},
    ],
}


class Binding(unittest.TestCase):
    """The name contract, checked without running anything."""

    def options(self) -> dict[str, object]:
        return {f.name: f for f in dataclasses.fields(Options)}

    def test_every_option_names_a_field(self) -> None:
        """A renamed flag would otherwise fail only when the command is run."""
        exposed = {p.name for p in cli.params if p.expose_value}
        self.assertEqual(exposed, set(self.options()))

    def test_the_defaults_agree(self) -> None:
        """Two copies of a default is one that can drift.

        Click leaves an unstated default as a sentinel and passes None for
        it, so only the ones actually spelled out on both sides compare --
        which here is every option carrying a number.
        """
        fields = {f.name: f.default for f in dataclasses.fields(Options) if f.default is not dataclasses.MISSING}
        compared = 0
        for param in cli.params:
            if not isinstance(param, click.Option) or param.is_flag or param.name not in fields:
                continue
            if isinstance(param.default, (int, float, str)):
                self.assertEqual(param.default, fields[param.name], param.name)
                compared += 1
        self.assertTrue(compared, "no default was actually compared")


class Pairs(unittest.TestCase):
    def test_a_known_name_parses(self) -> None:
        self.assertEqual(parse_pairs(("hbt-rs=/bin/hbt",), "--binary", "NAME=PATH", ["hbt-rs"]), {"hbt-rs": "/bin/hbt"})

    def test_an_unknown_name_is_refused(self) -> None:
        with self.assertRaisesRegex(core.BenchmarkError, "--binary"):
            parse_pairs(("nope=/bin/hbt",), "--binary", "NAME=PATH", ["hbt-rs"])

    def test_a_value_without_an_equals_is_refused(self) -> None:
        with self.assertRaisesRegex(core.BenchmarkError, "--revision"):
            parse_pairs(("hbt-rs",), "--revision", "NAME=REV", ["hbt-rs"])


class Invocation(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.runner = CliRunner()

    def test_report_only_renders_a_saved_document(self) -> None:
        saved = self.root / "results.json"
        saved.write_text(json.dumps(RESULTS), encoding="utf-8")
        result = self.runner.invoke(cli, ["--report-only", str(saved)])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("# hbt benchmark", result.output)
        self.assertIn("hbt-rs", result.output)

    def test_report_only_needs_a_file_that_exists(self) -> None:
        result = self.runner.invoke(cli, ["--report-only", str(self.root / "gone.json")])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not exist", result.output)

    def test_info_only_refuses_the_published_results_file(self) -> None:
        """A document with no timings must never become the published one."""
        with patch.object(core, "repo_root", return_value=self.root):
            result = self.runner.invoke(cli, ["--info-only"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--info-only writes no timings", result.output)

    def test_an_unknown_implementation_is_refused(self) -> None:
        (self.root / "benchmarks").mkdir()
        with (
            patch.object(core, "repo_root", return_value=self.root),
            patch.object(core, "implementations", return_value=["hbt-rs"]),
        ):
            result = self.runner.invoke(cli, ["--impl", "nope", "--info-only", "-o", str(self.root / "out.json")])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("unknown implementation(s): nope", result.output)


class EntryPoint(unittest.TestCase):
    """`python -m hbt.bench` runs; importing the same module must not."""

    def test_importing_the_entry_point_does_not_run_it(self) -> None:
        """A doctest collector or a `walk_packages` sweep imports it by name.

        Unguarded, Click would then parse that tool's argv and exit out from
        under it, which reads as the collector crashing.
        """
        sys.modules.pop("hbt.bench.__main__", None)
        with patch.object(sys, "argv", ["pytest", "--doctest-modules"]):
            importlib.import_module("hbt.bench.__main__")
