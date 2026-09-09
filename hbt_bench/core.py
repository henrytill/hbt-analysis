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
    store_path: str | None
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


def env_var(name: str) -> str:
    """The environment variable that overrides one implementation's binary."""
    return "HBT_BENCH_" + name.upper().replace("-", "_")


def discover(root: Path, names: list[str], overrides: dict[str, Path] | None = None) -> list[Impl]:
    """Find the built binaries, and record which build each one is.

    Three sources, in order: an explicit --binary override, the HBT_BENCH_*
    environment variable the root flake's wrapper sets, and finally the
    `result-hbt-*` symlink for ad-hoc use outside the flake.

    The store path and submodule revision are the provenance the org-babel
    notebook never captured: a timing means nothing without knowing which build
    produced it.
    """
    impls: list[Impl] = []
    overrides = overrides or {}
    for name in names:
        link = root / f"result-{name}"
        binary = overrides.get(name) or Path(os.environ.get(env_var(name), link / "bin" / "hbt"))
        if not binary.exists():
            print(f"{name}: no {binary}, skipped (nix build ./{name}# -o {link.name})", file=sys.stderr)
            continue
        # Resolving gives the same provenance whichever of the three sources
        # supplied the path: for the symlink it is what it points at, for the
        # flake wrapper it is already a store path.
        store = str(binary.resolve().parent.parent)
        rev = None
        if (root / name).is_dir():
            out = subprocess.run(
                ["git", "-C", str(root / name), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode == 0:
                rev = out.stdout.strip()
        impls.append(Impl(name, binary, store, rev))
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
        "implementations": [{"name": i.name, "store_path": i.store_path, "revision": i.revision} for i in impls],
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
