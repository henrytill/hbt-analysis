"""Tests for the conformance matrix.

The comparison is hbt.conformance's and is tested there.  What is tested here
is what this module adds: finding each implementation's corpus, keeping one
implementation's trouble in its own column, and the exit status.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import io
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hbt.bench import core
from hbt.bench.matrix import Options, cli, corpus_root, run
from hbt.conformance.corpus import CorpusError
from tests import binding

DOCUMENT = """version: 0.1.0
length: 0
value: []
"""

OTHER = """version: 0.1.0
length: 1
value:
- id: 0
  entity:
    uri: https://example.com/
    createdAt: 1700092800
    updatedAt: []
    names: []
    labels: []
  edges: []
"""


class Binding(unittest.TestCase):
    def test_every_option_names_a_field(self) -> None:
        binding.assert_every_option_names_a_field(self, cli, Options)

    def test_the_defaults_agree(self) -> None:
        binding.assert_the_defaults_agree(self, cli, Options)


def _submodule(gitmodules: Path, name: str, path: str, url: str) -> None:
    for key, value in (("path", path), ("url", url)):
        subprocess.run(["git", "config", "--file", str(gitmodules), f"submodule.{name}.{key}", value], check=True)


class CorpusRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "hbt-x").mkdir()
        self.gitmodules = self.root / "hbt-x" / ".gitmodules"

    def test_the_corpus_is_found_by_its_url_not_its_path(self) -> None:
        _submodule(self.gitmodules, "vendor", "vendor", "https://github.com/someone/else.git")
        _submodule(self.gitmodules, "core/test/data", "core/test/data", "https://github.com/henrytill/hbt-data.git")
        data = self.root / "hbt-x" / "core" / "test" / "data"
        data.mkdir(parents=True)
        (data / "README.md").touch()
        self.assertEqual(corpus_root(self.root, "hbt-x"), data)

    def test_no_corpus_submodule_is_an_error(self) -> None:
        _submodule(self.gitmodules, "vendor", "vendor", "https://github.com/someone/else.git")
        with self.assertRaisesRegex(CorpusError, "found none"):
            corpus_root(self.root, "hbt-x")

    def test_an_uninitialized_submodule_says_how_to_fix_it(self) -> None:
        _submodule(self.gitmodules, "hbt-data", "hbt-data", "https://github.com/henrytill/hbt-data")
        (self.root / "hbt-x" / "hbt-data").mkdir()
        with self.assertRaisesRegex(CorpusError, "submodule update"):
            corpus_root(self.root, "hbt-x")


class Run(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.corpus = self.root / "corpus"
        self.write_fixture(self.corpus)
        self.enterContext(patch.object(core, "repo_root", return_value=self.root))
        self.enterContext(patch.object(core, "implementations", return_value=["hbt-x", "hbt-y"]))

    def write_fixture(self, corpus: Path) -> None:
        (corpus / "markdown").mkdir(parents=True)
        (corpus / "markdown" / "a.input.md").write_text("# a\n", encoding="utf-8")
        (corpus / "markdown" / "a.expected.yaml").write_text(DOCUMENT, encoding="utf-8")

    def stub(self, name: str, output: str) -> str:
        path = self.root / name
        path.write_text(f'#!/bin/sh\ncat <<"EOF"\n{output}EOF\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def run_matrix(self, **kwargs: object) -> tuple[int, str]:
        out = io.StringIO()
        with patch("sys.stderr", io.StringIO()):
            status = run(Options(**kwargs), out)  # type: ignore[arg-type]
        return status, out.getvalue()

    def binaries(self) -> tuple[str, ...]:
        return (f"hbt-x={self.stub('good', DOCUMENT)}", f"hbt-y={self.stub('bad', OTHER)}")

    def test_a_divergence_fails_the_run_and_names_the_column(self) -> None:
        status, output = self.run_matrix(binary=self.binaries(), corpus=self.corpus)
        self.assertEqual(status, 1)
        self.assertRegex(output, r"markdown/a\s+PASS\s+FAIL")
        self.assertIn("hbt-y: ", output)
        self.assertNotIn("hbt-x: ", output)

    def test_a_waiver_applies_to_its_own_column_only(self) -> None:
        waivers = self.root / "waivers"
        waivers.write_text("markdown/a # broken\n", encoding="utf-8")
        status, output = self.run_matrix(binary=self.binaries(), corpus=self.corpus, waivers=(f"hbt-y={waivers}",))
        self.assertEqual(status, 0)
        self.assertRegex(output, r"markdown/a\s+PASS\s+XFAIL")

    def test_a_stale_waiver_fails_the_run(self) -> None:
        waivers = self.root / "waivers"
        waivers.write_text("markdown/gone # fixed long ago\n", encoding="utf-8")
        good = self.stub("good", DOCUMENT)
        status, output = self.run_matrix(
            binary=(f"hbt-x={good}", f"hbt-y={good}"), corpus=self.corpus, waivers=(f"hbt-x={waivers}",)
        )
        self.assertEqual(status, 1)
        self.assertIn("hbt-x waives unknown fixture markdown/gone", output)

    def test_quiet_drops_rows_everyone_passed(self) -> None:
        good = self.stub("good", DOCUMENT)
        status, output = self.run_matrix(binary=(f"hbt-x={good}", f"hbt-y={good}"), corpus=self.corpus, quiet=True)
        self.assertEqual(status, 0)
        self.assertNotIn("markdown/a", output)

    def test_each_implementation_runs_the_corpus_it_pins(self) -> None:
        """hbt-y's corpus is missing: that is its column's failure, not the run's."""
        (self.root / "hbt-x").mkdir()
        _submodule(self.root / "hbt-x" / ".gitmodules", "data", "data", "https://github.com/henrytill/hbt-data.git")
        self.write_fixture(self.root / "hbt-x" / "data")
        (self.root / "hbt-y").mkdir()
        status, output = self.run_matrix(binary=self.binaries())
        self.assertEqual(status, 1)
        self.assertRegex(output, r"markdown/a\s+PASS\s+-")
        self.assertRegex(output, r"hbt-y\s+unavailable: could not read")

    def test_list_runs_nothing(self) -> None:
        status, output = self.run_matrix(corpus=self.corpus, list_only=True)
        self.assertEqual(status, 0)
        self.assertEqual(output, "markdown/a\n")

    def test_a_filter_that_matches_nothing_is_refused(self) -> None:
        with self.assertRaisesRegex(core.BenchmarkError, "no fixture matches"):
            self.run_matrix(corpus=self.corpus, patterns=("nope",))
