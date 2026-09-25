"""Holding hbt implementations to each other on inputs nobody wrote expectations for.

A library, the way :mod:`hbt.bench` and :mod:`hbt.conformance` are.  It is
handed each implementation's binary by name, and generates documents, runs every
implementation over each, and reports the ones they do not all read the same
way.  Which implementations exist, and where their binaries come from, is the
business of :mod:`hbt.analysis`, which drives this.

Conformance holds each implementation to the corpus, so it can only find a
disagreement someone already thought to pin.  A disagreement found here is
where the next fixture comes from: it is settled in hbt-data, pinned there, and
from then on conformance holds everyone to it.

No implementation is the reference.  Which side of a disagreement is right is
the question it raises, not one this answers.  Agreement is `hbt.conformance`'s
own comparison, imported rather than restated, so two outputs this calls the
same would pass as each other's expectation.  Any two failures agree: the
implementations word their errors differently, and whether a document is
refused is the question, not how.
"""

from hbt.fuzz.generators import GENERATORS, Generator
from hbt.fuzz.report import report
from hbt.fuzz.search import Disagreement, Trial, search
from hbt.fuzz.verdict import Atom, Failure, Verdict, atoms, execute, split

__all__ = [
    "GENERATORS",
    "Atom",
    "Disagreement",
    "Failure",
    "Generator",
    "Trial",
    "Verdict",
    "atoms",
    "execute",
    "report",
    "search",
    "split",
]
