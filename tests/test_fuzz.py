"""Tests for the fuzzer.

Generating and shrinking are Hypothesis's, and comparing two Collections is
hbt.conformance's; both are tested where they live.  What is tested here is
what this module adds: taking a disagreement apart into atoms, keeping each
round on one atom through shrinking, and the report and exit status.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hbt.analysis import implementations
from hbt.analysis.commands import CommandError, fuzz
from hbt.analysis.commands.fuzz import Options, run
from hbt.analysis.implementations import Selection
from hbt.fuzz import Atom, Disagreement, Failure, Trial, Verdict, atoms
from tests.stubs import DOCUMENT, executable


def _collection(**entity: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"uri": "https://a.com/", "updatedAt": [], "names": [], "labels": [], **entity}
    return {"version": "0.1.0", "length": 1, "value": [{"id": 0, "entity": node, "edges": []}]}


class Atoms(unittest.TestCase):
    def test_agreement_has_none(self) -> None:
        self.assertEqual(atoms({"a": _collection(), "b": _collection()}), set())

    def test_failing_everywhere_is_agreement(self) -> None:
        self.assertEqual(atoms({"a": Failure("exit 1", ("x",)), "b": Failure("exit 2", ("y",))}), set())

    def test_one_implementation_out_is_one_atom_not_one_per_pair(self) -> None:
        verdicts: dict[str, Verdict] = {
            "a": _collection(),
            "b": _collection(),
            "c": _collection(names=["Foo"]),
        }
        expected = {Atom("differs", "$.value[].entity.names[]", (("a", "b"), ("c",)))}
        self.assertEqual(atoms(verdicts), expected)

    def test_independent_fields_are_separate_atoms(self) -> None:
        verdicts: dict[str, Verdict] = {"a": _collection(), "b": _collection(names=["Foo"], labels=["Bar"])}
        self.assertEqual(
            {atom.subject for atom in atoms(verdicts)}, {"$.value[].entity.names[]", "$.value[].entity.labels[]"}
        )

    def test_the_same_failure_is_one_atom(self) -> None:
        failure = Failure("exit 1", ("missing URL",))
        verdicts: dict[str, Verdict] = {"a": _collection(), "b": failure, "c": failure}
        self.assertEqual(atoms(verdicts), {Atom("fails", "exit 1\nmissing URL", (("b", "c"),))})


class Target(unittest.TestCase):
    """A round fails only on its own atom, so shrinking cannot slip to another."""

    def setUp(self) -> None:
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.trial = Trial({"a": Path("never-run")}, ".md", 1.0, scratch)
        self.email: dict[str, Verdict] = {"a": _collection(), "b": Failure("exit 1", ("missing URL",))}
        self.undated: dict[str, Verdict] = {"a": _collection(), "b": Failure("exit 1", ("missing date",))}
        judged = {"email": self.email, "undated": self.undated, "both": self.email, "agreed": {"a": _collection()}}
        self.enterContext(patch.object(self.trial, "judge", side_effect=judged.__getitem__))

    def test_the_first_new_atom_becomes_the_target(self) -> None:
        with self.assertRaises(Disagreement) as raised:
            self.trial.check("email")
        self.assertEqual(len(atoms(self.email)), 1)
        expected = next(iter(atoms(self.email)))
        self.assertEqual(raised.exception.atom, expected)
        self.assertEqual(self.trial.target, expected)

    def test_another_new_atom_passes_once_there_is_a_target(self) -> None:
        with self.assertRaises(Disagreement):
            self.trial.check("email")
        self.trial.check("undated")
        with self.assertRaises(Disagreement):
            self.trial.check("both")

    def test_a_known_atom_does_not_become_the_target(self) -> None:
        self.trial.known |= atoms(self.email)
        self.trial.check("email")
        self.trial.check("agreed")
        self.assertIsNone(self.trial.target)


class Run(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(fuzz, "repo_root", return_value=self.root))
        self.enterContext(patch.object(implementations, "implementations", return_value=["hbt-x", "hbt-y"]))

    def empty(self) -> str:
        return executable(self.root, "empty", f'cat <<"EOF"\n{DOCUMENT}EOF\n')

    def disagreeing(self) -> tuple[str, ...]:
        """hbt-x reads everything; hbt-y refuses an email autolink, naming its input as hbt-rs does."""
        picky = executable(
            self.root,
            "picky",
            f'if grep -q "<me@ex.com>" "$3"; then echo "Could not parse $3: missing URL" >&2; exit 1; fi\n'
            f'cat <<"EOF"\n{DOCUMENT}EOF\n',
        )
        return (f"hbt-x={self.empty()}", f"hbt-y={picky}")

    def fuzz(self, binary: tuple[str, ...], keep: Path | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with patch("sys.stderr", io.StringIO()):
            status = run(Selection(binary=binary), Options(seed=0, examples=50, keep=keep), out)
        return status, out.getvalue()

    def test_agreement_passes(self) -> None:
        empty = self.empty()
        status, output = self.fuzz((f"hbt-x={empty}", f"hbt-y={empty}"))
        self.assertEqual(status, 0)
        self.assertIn("no disagreement found", output)

    def test_a_disagreement_fails_the_run_with_its_shrunk_input(self) -> None:
        status, output = self.fuzz(self.disagreeing())
        self.assertEqual(status, 1)
        self.assertIn("disagreement 1: hbt-y fail(s) where the rest do not", output)
        self.assertIn("1 disagreement(s) found", output)
        shown = [line[6:] for line in output.splitlines() if line.startswith("    | ")]
        self.assertEqual([line for line in shown if line and not line.startswith("# ")], ["<me@ex.com>"], output)
        # Every document is the same relative path, so its diagnostic reads the same each time.
        self.assertIn("Could not parse input.md: missing URL", output)

    def test_a_seed_reproduces_a_run(self) -> None:
        binaries = self.disagreeing()
        self.assertEqual(self.fuzz(binaries), self.fuzz(binaries))

    def test_keep_writes_each_disagreements_input(self) -> None:
        kept = self.root / "kept"
        self.fuzz(self.disagreeing(), keep=kept)
        (written,) = kept.iterdir()
        self.assertEqual(written.name, "fuzz-0-1.input.md")
        self.assertIn("<me@ex.com>", written.read_text(encoding="utf-8"))

    def test_each_disagreement_is_reported_with_its_own_input(self) -> None:
        """Two refusals, where deleting the date turns the email one into the undated one.

        A round that slipped would still pass this when a later round finds the
        email refusal again; `Target` is what pins the mechanism.
        """
        picky = executable(
            self.root,
            "picky",
            'if ! grep -q "^# " "$3"; then echo "missing date" >&2; exit 1; fi\n'
            'if grep -q "<me@ex.com>" "$3"; then echo "missing URL" >&2; exit 1; fi\n'
            f'cat <<"EOF"\n{DOCUMENT}EOF\n',
        )
        status, output = self.fuzz((f"hbt-x={self.empty()}", f"hbt-y={picky}"))
        self.assertEqual(status, 1)
        reports = output.split("\ndisagreement ")[1:]
        email = [r for r in reports if "missing URL" in r]
        self.assertEqual(len(email), 1, output)
        # Its own input: the email link, and the date that keeps it from being the undated one.
        self.assertIn("<me@ex.com>", email[0])
        self.assertIn("| # ", email[0])
        self.assertTrue(any("missing date" in r for r in reports), output)

    def test_differences_are_shown_when_the_first_implementation_failed(self) -> None:
        self.enterContext(patch.object(implementations, "implementations", return_value=["hbt-a", "hbt-x", "hbt-y"]))
        one = DOCUMENT.replace(
            "length: 0\nvalue: []", "length: 1\nvalue:\n- id: 0\n  entity:\n    uri: https://a.com/\n  edges: []"
        )
        failing = executable(self.root, "failing", "exit 1\n")
        other = executable(self.root, "other", f'cat <<"EOF"\n{one}EOF\n')
        _, output = self.fuzz((f"hbt-a={failing}", f"hbt-x={self.empty()}", f"hbt-y={other}"))
        self.assertIn("hbt-y against hbt-x:", output)

    def test_one_binary_is_refused(self) -> None:
        with self.assertRaisesRegex(CommandError, "at least two"):
            self.fuzz((f"hbt-x={self.empty()}", f"hbt-y={self.root / 'missing'}"))
