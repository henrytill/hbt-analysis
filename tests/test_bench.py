"""Tests for the benchmark's entity-count gate.

The timings need hyperfine and are not tested here.  What is tested is the
--info stage's one judgement: a row where the implementations count
differently fails the run, and a row some of them cannot read does not.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hbt.analysis import implementations
from hbt.analysis.commands import bench
from hbt.analysis.commands.bench import Options, run
from hbt.analysis.implementations import Impl, Selection
from hbt.bench import Input, Pair, disagreements
from tests.stubs import executable

MARKDOWN = Input("markdown", Path("markdown.md"))


def pair(name: str, entities: int | None = None, error: str | None = None) -> Pair:
    return Pair(Impl(name), MARKDOWN.name, entities, error)


class Disagreements(unittest.TestCase):
    def test_equal_counts_agree(self) -> None:
        self.assertEqual(disagreements([pair("hbt-x", 3), pair("hbt-y", 3)], [MARKDOWN]), [])

    def test_different_counts_name_who_counted_what(self) -> None:
        found = disagreements([pair("hbt-x", 3), pair("hbt-y", 4), pair("hbt-z", 3)], [MARKDOWN])
        self.assertEqual(found, ["markdown: 3 (hbt-x, hbt-z), 4 (hbt-y)"])

    def test_an_implementation_that_fails_is_left_out(self) -> None:
        """Only one implementation reads YAML; that row is not a disagreement."""
        found = disagreements([pair("hbt-x", 3), pair("hbt-y", error="no parser for extension")], [MARKDOWN])
        self.assertEqual(found, [])


class Run(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "benchmarks").mkdir()
        (self.root / "markdown.md").write_text("# a\n", encoding="utf-8")
        self.corpus = self.root / "corpus.toml"
        self.corpus.write_text('[[input]]\nname = "markdown"\npath = "markdown.md"\n', encoding="utf-8")
        self.output = self.root / "info.json"
        self.enterContext(patch.object(bench, "repo_root", return_value=self.root))
        self.enterContext(patch.object(implementations, "implementations", return_value=["hbt-x", "hbt-y"]))

    def counting(self, name: str, entities: int) -> str:
        script = f'echo "$2: {entities} entities"\n'
        return f"{name}={executable(self.root, name, script)}"

    def run_info(self, *binary: str) -> tuple[int, str]:
        err = io.StringIO()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", err):
            status = run(Selection(binary=binary), Options(corpus=self.corpus, output=self.output, info_only=True))
        return status, err.getvalue()

    def test_agreeing_counts_pass(self) -> None:
        status, _ = self.run_info(self.counting("hbt-x", 3), self.counting("hbt-y", 3))
        self.assertEqual(status, 0)

    def test_disagreeing_counts_fail_after_writing_the_document(self) -> None:
        status, err = self.run_info(self.counting("hbt-x", 3), self.counting("hbt-y", 4))
        self.assertEqual(status, 1)
        self.assertIn("entity counts disagree on markdown: 3 (hbt-x), 4 (hbt-y)", err)
        results = json.loads(self.output.read_text(encoding="utf-8"))["results"]
        self.assertEqual([r["entities"] for r in results], [3, 4])


if __name__ == "__main__":
    unittest.main()
