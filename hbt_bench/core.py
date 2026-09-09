"""Running the benchmark matrix.

Two phases by design. `--info` is run once per (implementation, input) pair
first, to learn the entity count and to find out whether that implementation
handles the format at all; a pair that fails is recorded as unsupported and
left out of the timings instead of aborting the run. Then each input is
benchmarked with a single hyperfine invocation naming every implementation that
worked, so hyperfine computes the relative ranking itself.

The results are the durable artifact; rendering is a separate step that reads
them back, so a report can be regenerated without re-running anything.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The submodule directory name doubles as the implementation name and as the
# `result-hbt-*` symlink suffix.
IMPLEMENTATIONS = ["hbt-hs", "hbt-go", "hbt-ocaml", "hbt-rs"]

# Go uses stdlib `flag`, which accepts the double-dashed spelling too, so one
# invocation shape works everywhere. The reply text differs between
# implementations -- Go prints "Collection contains N entities", the others
# "<path>: N entities" -- hence the loose match.
INFO_FLAG = "--info"
ENTITIES_RE = re.compile(r"(\d+)\s+entities")


def _no_timing() -> dict[str, Any]:
    """Named so the default_factory carries a type pyright can see."""
    return {}


class BenchmarkError(Exception):
    """A condition that should stop the run with a message, not a traceback."""


@dataclass
class Impl:
    name: str
    binary: Path
    store_path: str
    # What the binary says about itself, verbatim; None if it has no --version.
    version: str | None
    # Only ever set from a source that knows which build this is -- the flake
    # wrapper passes the rev of the input it built. Never guessed.
    revision: str | None


@dataclass
class Input:
    name: str
    path: Path


@dataclass
class Pair:
    """One (implementation, input) cell of the matrix."""

    impl: str
    input: str
    entities: int | None = None
    error: str | None = None
    timing: dict[str, Any] = field(default_factory=_no_timing)

    @property
    def ok(self) -> bool:
        """Whether this pair is still eligible to be benchmarked."""
        return self.error is None


def repo_root() -> Path:
    """The top of the working tree this is being run from."""
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


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
    """Read the corpus TOML, expanding `~` and resolving relative paths."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as exc:
        raise BenchmarkError(f"{path}: no such corpus file") from exc
    inputs: list[Input] = []
    for entry in data.get("input", []):
        p = Path(os.path.expanduser(entry["path"]))
        if not p.is_absolute():
            p = root / p
        inputs.append(Input(entry["name"], p))
    if not inputs:
        raise BenchmarkError(f"{path}: no [[input]] entries")
    return inputs


def build(root: Path, names: list[str]) -> None:
    """Refresh the `result-hbt-*` symlinks from each implementation's flake."""
    for name in names:
        print(f"building {name} ...", file=sys.stderr)
        subprocess.run(["nix", "build", f"./{name}#", "-o", f"result-{name}"], cwd=root, check=True)


def self_reported_version(binary: Path) -> str | None:
    """What `binary --version` prints, or None if it does not support it."""
    out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return None
    first = out.stdout.strip().splitlines()
    return first[0] if first else None


def discover(root: Path, names: list[str], overrides: dict[str, Path], revisions: dict[str, str]) -> list[Impl]:
    """Find the built binaries, and record which build each one actually is.

    The binary comes from an explicit --binary override -- which is how the
    root flake's wrapper points at what it built -- or otherwise from the
    `result-hbt-*` symlink, for ad-hoc use outside the flake.

    Provenance is taken from the artifact, never inferred from the working
    tree.  The submodule's HEAD is *not* the revision of the binary: a
    `result-*` symlink is whatever was last built there, and under the flake
    the binary comes from flake.lock, which AGENTS.md documents as routinely
    behind the gitlink.  Measured on this checkout, all three implementations
    that support --version disagreed with their gitlink and one reported
    -dirty.  So the store path (exact, from the binary itself) and the
    binary's own --version string are recorded, and `revision` is set only
    when a caller knows it authoritatively.
    """
    impls: list[Impl] = []
    for name in names:
        link = root / f"result-{name}"
        binary = overrides.get(name, link / "bin" / "hbt")
        if not binary.exists():
            print(f"{name}: no {binary}, skipped (nix build ./{name}# -o {link.name})", file=sys.stderr)
            continue
        store = str(binary.resolve().parent.parent)
        impls.append(Impl(name, binary, store, self_reported_version(binary), revisions.get(name)))
    return impls


def verify(impls: list[Impl], inputs: list[Input]) -> list[Pair]:
    """Run --info once per pair, for the entity count and to prune what fails."""
    pairs: list[Pair] = []
    for inp in inputs:
        for impl in impls:
            pair = Pair(impl.name, inp.name)
            if not inp.path.exists():
                pair.error = "input missing"
            else:
                out = subprocess.run(
                    [str(impl.binary), INFO_FLAG, str(inp.path)], capture_output=True, text=True, check=False
                )
                if out.returncode != 0:
                    detail = (out.stderr or out.stdout).strip().splitlines()
                    pair.error = detail[0] if detail else f"exit {out.returncode}"
                else:
                    match = ENTITIES_RE.search(out.stdout)
                    if match:
                        pair.entities = int(match.group(1))
                    else:
                        pair.error = "no entity count in --info output"
            pairs.append(pair)
    return pairs


def benchmark(pairs: list[Pair], impls: list[Impl], inputs: list[Input], warmup: int, min_runs: int | None) -> None:
    """Time each input across every implementation that handled it."""
    by_name = {i.name: i for i in impls}
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
                cmd += ["-n", pair.impl, shlex.join([str(by_name[pair.impl].binary), INFO_FLAG, str(inp.path)])]
            print(f"benchmarking {inp.name} ...", file=sys.stderr)
            # hyperfine writes its progress display and summary to stdout, not
            # stderr. Send it to stderr so stdout carries nothing but the
            # report, and `hbt-bench > report.md` stays clean.
            subprocess.run(cmd, check=True, stdout=sys.stderr)
            with open(tmp.name, encoding="utf-8") as f:
                results = {r["command"]: r for r in json.load(f)["results"]}
        for pair in working:
            result = results.get(pair.impl)
            if result is None:
                pair.error = "no hyperfine result"
                continue
            pair.timing = {
                k: result[k] for k in ("mean", "stddev", "median", "min", "max", "user", "system") if k in result
            }
            pair.timing["runs"] = len(result.get("times", []))


def collect(impls: list[Impl], inputs: list[Input], pairs: list[Pair]) -> dict[str, Any]:
    """Assemble the serializable results document."""
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {
            "node": platform.node(),
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "implementations": [
            {"name": i.name, "store_path": i.store_path, "version": i.version, "revision": i.revision} for i in impls
        ],
        "inputs": [{"name": i.name, "path": str(i.path)} for i in inputs],
        "results": [
            {
                "implementation": p.impl,
                "input": p.input,
                "entities": p.entities,
                "error": p.error,
                "timing": p.timing or None,
            }
            for p in pairs
        ],
    }
