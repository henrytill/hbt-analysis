"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hbt_bench import __version__, core, report

DESCRIPTION = "Benchmark the four hbt implementations against a shared corpus."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the parser and parse `argv` (default: sys.argv)."""
    parser = argparse.ArgumentParser(prog="hbt-bench", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--corpus", type=Path, help="corpus TOML (default: benchmarks/corpus.toml)")
    parser.add_argument("-o", "--output", type=Path, help="results JSON (default: benchmarks/results.json)")
    parser.add_argument("-r", "--report", type=Path, help="write the Markdown report here (default: stdout)")
    parser.add_argument("--report-only", type=Path, metavar="RESULTS", help="re-render a saved results file")
    parser.add_argument("--build", action="store_true", help="nix build each implementation first")
    parser.add_argument("--impl", action="append", metavar="NAME", help="limit to this implementation (repeatable)")
    parser.add_argument(
        "--binary",
        action="append",
        metavar="NAME=PATH",
        default=[],
        help="use this binary for NAME instead of result-NAME/bin/hbt (repeatable)",
    )
    parser.add_argument(
        "--revision",
        action="append",
        metavar="NAME=REV",
        default=[],
        help="record REV as the revision NAME's binary was built from (repeatable)",
    )
    parser.add_argument("--warmup", type=int, default=20, help="hyperfine warmup runs (default: 20)")
    parser.add_argument("--min-runs", type=int, help="hyperfine minimum runs (default: hyperfine's own)")
    return parser.parse_args(argv)


def parse_pairs(specs: list[str], flag: str, shape: str) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments, validating NAME."""
    parsed: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or name not in core.IMPLEMENTATIONS:
            raise core.BenchmarkError(f"{flag} expects {shape} with a known NAME, got {spec!r}")
        parsed[name] = value
    return parsed


def write_report(data: dict[str, Any], path: Path | None) -> int:
    """Render `data` and write it, defaulting to stdout when no path is given."""
    text = report.render(data)
    if path is None:
        sys.stdout.write(text)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)
    return 0


def run(args: argparse.Namespace) -> int:
    """Run the benchmark (or just re-render) and write the outputs."""
    if args.report_only:
        # Deliberately before repo_root(): re-rendering must work outside a git
        # checkout, because the Nix derivation that builds the published page
        # does exactly that in the sandbox.
        with args.report_only.open(encoding="utf-8") as f:
            data = json.load(f)
        return write_report(data, args.report)

    root = core.repo_root()
    bench_dir = root / "benchmarks"

    names = args.impl or core.IMPLEMENTATIONS
    unknown = set(names) - set(core.IMPLEMENTATIONS)
    if unknown:
        raise core.BenchmarkError(f"unknown implementation(s): {', '.join(sorted(unknown))}")
    overrides = {n: Path(v) for n, v in parse_pairs(args.binary, "--binary", "NAME=PATH").items()}
    revisions = parse_pairs(args.revision, "--revision", "NAME=REV")
    # --build refreshes the result-* symlinks, which an override bypasses.
    if args.build:
        core.build(root, [n for n in names if n not in overrides])
    impls = core.discover(root, names, overrides, revisions)
    if not impls:
        raise core.BenchmarkError("no built implementations found; try --build")
    inputs = core.load_corpus(root, args.corpus or bench_dir / "corpus.toml")
    pairs = core.verify(impls, inputs)
    core.benchmark(pairs, impls, inputs, args.warmup, args.min_runs)
    data = core.collect(impls, inputs, pairs)

    output = args.output or bench_dir / "results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}", file=sys.stderr)

    # No default path: results.json is the only file this leaves in the tree.
    # Markdown and HTML are translations of it -- report.md goes to stdout
    # unless asked for, and the HTML is produced by `nix build .#site`.
    return write_report(data, args.report)
