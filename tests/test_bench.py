"""Tests for the benchmark's entity-count gate.

The timings need hyperfine and are not tested here.  What is tested is the
--info stage's one judgement: a row where the implementations count
differently is named in the report and fails the run, and a row some of them
cannot read is neither.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hbt.analysis import implementations
from hbt.analysis.commands import bench
from hbt.analysis.commands.bench import Options, run
from hbt.analysis.implementations import Selection
from hbt.bench import FORMAT_VERSION, disagreements, render
from tests.stubs import executable


def document(*results: tuple[str, str, int | None]) -> dict[str, Any]:
    """A results document with one cell per (implementation, input, entities); None is a failed cell."""
    impls = sorted({r[0] for r in results})
    inputs = sorted({r[1] for r in results})
    return {
        "version": FORMAT_VERSION,
        "generated": "2026-01-01T00:00:00+00:00",
        "hyperfine": None,
        "host": {"node": "somewhere", "machine": "x86_64", "system": "Linux", "release": "6"},
        "implementations": [
            {"name": n, "store_path": None, "version": None, "revision": None, "error": None} for n in impls
        ],
        "inputs": [{"name": n, "path": f"{n}.in"} for n in inputs],
        "results": [
            {
                "implementation": i,
                "input": n,
                "entities": e,
                "error": None if e is not None else "no parser",
                "timing": None,
            }
            for i, n, e in results
        ],
    }


class Disagreements(unittest.TestCase):
    def test_equal_counts_agree(self) -> None:
        self.assertEqual(disagreements(document(("hbt-x", "markdown", 3), ("hbt-y", "markdown", 3))), [])

    def test_different_counts_name_who_counted_what(self) -> None:
        data = document(("hbt-x", "markdown", 3), ("hbt-y", "markdown", 4), ("hbt-z", "markdown", 3))
        self.assertEqual(disagreements(data), ["markdown: 3 (hbt-x, hbt-z), 4 (hbt-y)"])

    def test_an_implementation_without_a_count_is_left_out(self) -> None:
        self.assertEqual(disagreements(document(("hbt-x", "yaml", 3), ("hbt-y", "yaml", None))), [])

    def test_the_report_names_a_disagreeing_row(self) -> None:
        text = render(document(("hbt-x", "markdown", 3), ("hbt-y", "markdown", 4)))
        self.assertIn("- markdown: 3 (hbt-x), 4 (hbt-y)", text)

    def test_an_agreeing_report_names_none(self) -> None:
        self.assertNotIn("These rows disagree", render(document(("hbt-x", "markdown", 3), ("hbt-y", "markdown", 3))))


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
