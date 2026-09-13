"""Running the benchmark matrix.

Two phases by design. `--info` is run once per (implementation, input) pair
first, to learn the entity count and to find out whether that implementation
handles the format at all; a pair that fails is recorded as unsupported and
left out of the timings instead of aborting the run. Then each input is
benchmarked with a single hyperfine invocation naming every implementation that
worked, so every command on a row is measured under the same conditions. The
ratios in the report are not hyperfine's -- its summary goes to stderr and is
discarded; report.py recomputes them from the exported means, a ratio being a
presentation concern.
"""

from __future__ import annotations

import json
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from hbt.bench.results import FORMAT_VERSION, BenchmarkError

# Go uses stdlib `flag`, which accepts the double-dashed spelling too, so one
# invocation shape works everywhere. The reply text differs between
# implementations -- Go prints "Collection contains N entities", the others
# "<path>: N entities" -- hence the loose match.
INFO_FLAG = "--info"
ENTITIES_RE = re.compile(r"(\d+)\s+entities")


class Implementation(Protocol):
    """What a benchmark needs to know about an implementation.

    Fields rather than a method that serializes them, so the document's
    implementation entries are spelled here, beside FORMAT_VERSION, rather
    than by whoever supplies an implementation.  `binary` is None when none
    could be found, and `error` then says why; the document still records it.
    """

    name: str
    binary: Path | None
    store_path: str | None
    version: str | None
    revision: str | None
    error: str | None


@dataclass
class Input:
    name: str
    path: Path


@dataclass
class Pair:
    """One (implementation, input) cell of the matrix.

    Carries the implementation rather than its name so benchmark() does not
    have to rebuild a name-to-implementation index for state verify() already
    had in hand.
    """

    impl: Implementation
    input: str
    entities: int | None = None
    error: str | None = None
    timing: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """Whether --info succeeded, and so whether this pair can still be timed."""
        return self.error is None

    def serialize(self) -> dict[str, Any]:
        """The document's view of this cell."""
        return {
            "implementation": self.impl.name,
            "input": self.input,
            "entities": self.entities,
            "error": self.error,
            "timing": self.timing,
        }


def hyperfine_cmd() -> list[str]:
    """Prefer hyperfine on PATH; fall back to fetching it through Nix.

    The flake puts it on PATH, so the fallback is for running the harness
    outside a dev shell.
    """
    if shutil.which("hyperfine"):
        return ["hyperfine"]
    if shutil.which("nix"):
        return ["nix", "run", "nixpkgs#hyperfine", "--"]
    raise BenchmarkError("neither hyperfine nor nix found on PATH")


def load_corpus(root: Path, path: Path) -> list[Input]:
    """Read the corpus TOML, expanding `~` and resolving relative paths against `root`."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as exc:
        raise BenchmarkError(f"{path}: no such corpus file") from exc
    inputs: list[Input] = []
    for entry in data.get("input", []):
        # `/` returns the right operand when it is absolute, so this covers
        # both the absolute and repo-relative cases.
        inputs.append(Input(entry["name"], root / Path(entry["path"]).expanduser()))
    if not inputs:
        raise BenchmarkError(f"{path}: no [[input]] entries")
    return inputs


def reported_version(cmd: list[str]) -> str | None:
    """The first line of `cmd --version`, or None if it does not support it.

    One routine for both the implementations and hyperfine itself: they are
    recorded for the same reason, and normalising them differently is how the
    two drift.
    """
    out = subprocess.run(cmd + ["--version"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return None
    lines = out.stdout.strip().splitlines()
    return lines[0] if lines else None


def verify(impls: Sequence[Implementation], inputs: Sequence[Input]) -> list[Pair]:
    """Run --info once per pair, for the entity count and to prune what fails."""
    pairs: list[Pair] = []
    runnable = [i for i in impls if i.binary is not None]
    for inp in inputs:
        # Depends only on the input, so it does not belong inside the per-impl
        # loop where it pushed the interesting path two levels deeper.
        if not inp.path.exists():
            pairs += [Pair(impl, inp.name, error="input missing") for impl in runnable]
            continue
        for impl in runnable:
            pair = Pair(impl, inp.name)
            out = subprocess.run(
                [str(impl.binary), INFO_FLAG, str(inp.path)], capture_output=True, text=True, check=False
            )
            match = ENTITIES_RE.search(out.stdout) if out.returncode == 0 else None
            if match:
                pair.entities = int(match.group(1))
            elif out.returncode == 0:
                pair.error = "no entity count in --info output"
            else:
                detail = (out.stderr or out.stdout).strip().splitlines()
                pair.error = detail[0] if detail else f"exit {out.returncode}"
            pairs.append(pair)
    return pairs


def benchmark(pairs: Sequence[Pair], inputs: Sequence[Input], warmup: int, min_runs: int | None) -> str | None:
    """Time each input across every implementation that handled it.

    Returns the hyperfine that did it, asked here rather than from collect():
    the version belongs to the run, and this is the function that made it.
    That keeps collect() a pure mapping from collected state to the document.
    """
    hyperfine = hyperfine_cmd()
    for inp in inputs:
        working = [p for p in pairs if p.input == inp.name and p.ok]
        if not working:
            print(f"{inp.name}: nothing to benchmark, skipped", file=sys.stderr)
            continue
        # --export-json has no stdout spelling, so it goes through a temp file.
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            # -N runs each command directly instead of under `sh -c`. The fastest
            # implementations here are a few milliseconds, the same order as shell
            # startup, so leaving the shell in would measure mostly sh. hyperfine
            # splits the command string itself, hence the quoting.
            cmd = hyperfine + ["-N", "--warmup", str(warmup), "--export-json", tmp.name]
            if min_runs:
                cmd += ["--min-runs", str(min_runs)]
            for pair in working:
                cmd += ["-n", pair.impl.name, shlex.join([str(pair.impl.binary), INFO_FLAG, str(inp.path)])]
            print(f"benchmarking {inp.name} ...", file=sys.stderr)
            # hyperfine writes its progress display and summary to stdout, not
            # stderr. Send it to stderr so stdout carries nothing but the
            # report, and `hbt-analysis bench > report.md` stays clean.
            try:
                subprocess.run(cmd, check=True, stdout=sys.stderr)
            except subprocess.CalledProcessError as exc:
                # hyperfine exits non-zero if any command fails on any of its
                # runs, and writes no export when it does. verify() has already
                # pruned everything that fails reproducibly, so getting here
                # means something intermittent -- record it against this input
                # and keep the inputs already measured, rather than losing the
                # whole run to the last one.
                for pair in working:
                    pair.error = f"hyperfine exited {exc.returncode}"
                continue
            with open(tmp.name, encoding="utf-8") as f:
                results = {r["command"]: r for r in json.load(f)["results"]}
        for pair in working:
            result = results.get(pair.impl.name)
            if result is None:
                pair.error = "no hyperfine result"
                continue
            timing = {k: result[k] for k in ("mean", "stddev", "median", "min", "max", "user", "system") if k in result}
            timing["runs"] = len(result.get("times", []))
            pair.timing = timing
    return reported_version(hyperfine)


def collect(
    impls: Sequence[Implementation], inputs: Sequence[Input], pairs: Sequence[Pair], hyperfine: str | None
) -> dict[str, Any]:
    """Assemble the serializable results document.

    Every input is passed in: this is a pure mapping from collected state to
    the document, and nothing it records is discovered by running anything.
    """
    return {
        "version": FORMAT_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hyperfine": hyperfine,
        "host": {
            "node": platform.node(),
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "implementations": [
            {"name": i.name, "store_path": i.store_path, "version": i.version, "revision": i.revision, "error": i.error}
            for i in impls
        ],
        "inputs": [{"name": i.name, "path": str(i.path)} for i in inputs],
        "results": [p.serialize() for p in pairs],
    }
