"""Benchmarking hbt implementations: time them over a corpus, record the results, render a report.

A library, the way :mod:`hbt.conformance` is.  It is handed implementations --
a name, a binary and the provenance to record, see :class:`Implementation` --
and runs them.  Which implementations exist, and where their binaries come
from, is the business of :mod:`hbt.analysis`, which drives this.
"""

from hbt.bench.report import render
from hbt.bench.results import FORMAT_VERSION, BenchmarkError, dump_results, load_results
from hbt.bench.timing import Implementation, Input, Pair, benchmark, collect, load_corpus, reported_version, verify

__all__ = [
    "FORMAT_VERSION",
    "BenchmarkError",
    "Implementation",
    "Input",
    "Pair",
    "benchmark",
    "collect",
    "dump_results",
    "load_corpus",
    "load_results",
    "render",
    "reported_version",
    "verify",
]
