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
from typing import Any

# Go uses stdlib `flag`, which accepts the double-dashed spelling too, so one
# invocation shape works everywhere. The reply text differs between
# implementations -- Go prints "Collection contains N entities", the others
# "<path>: N entities" -- hence the loose match.
INFO_FLAG = "--info"
ENTITIES_RE = re.compile(r"(\d+)\s+entities")

# Bumped when the shape of the results document changes incompatibly.  The
# thing being benchmarked versions its own serialized Collection against
# ^0.1.0; this file is committed, re-rendered by a Nix derivation on every
# push, and explicitly meant to be re-read later, so it gets the same
# treatment rather than being read with bare subscripts forever.
FORMAT_VERSION = "0.1.0"


class BenchmarkError(Exception):
    """A condition that should stop the run with a message, not a traceback."""


@dataclass
class Impl:
    """One implementation, whether or not a binary for it was found."""

    name: str
    binary: Path | None = None
    store_path: str | None = None
    # What the binary says about itself, verbatim; None if it has no --version.
    version: str | None = None
    # Only ever set from a source that knows which build this is -- the flake
    # wrapper passes the rev of the input it built. Never guessed.
    revision: str | None = None
    # Why there is no binary. An implementation that could not be found stays
    # in the document as a column of blanks with a reason, the way a failed
    # (implementation, input) pair does; dropping it would make a partial run
    # indistinguishable from a complete one.
    error: str | None = None

    @property
    def available(self) -> bool:
        """Whether this implementation can actually be run."""
        return self.binary is not None

    def serialize(self) -> dict[str, Any]:
        """The document's view of this implementation."""
        return {
            "name": self.name,
            "store_path": self.store_path,
            "version": self.version,
            "revision": self.revision,
            "error": self.error,
        }


@dataclass
class Input:
    name: str
    path: Path


@dataclass
class Pair:
    """One (implementation, input) cell of the matrix.

    Carries the Impl rather than its name so benchmark() does not have to
    rebuild a name-to-Impl index for state verify() already had in hand.
    """

    impl: Impl
    input: str
    entities: int | None = None
    error: str | None = None
    timing: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """Whether this pair is still eligible to be benchmarked."""
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


def implementations(root: Path) -> list[str]:
    """The implementations, read from .gitmodules.

    The submodule directory name doubles as the implementation name and as the
    `result-hbt-*` symlink suffix, so .gitmodules is the one place that already
    knows this.  scripts/update-submodules.sh and scripts/open-submodule-pr.sh
    both read it the same way, deliberately, so that neither carries a list to
    keep up to date.

    Sorted, because .gitmodules is in the order entries happened to be added
    and the report's column order should not be.
    """
    out = subprocess.run(
        ["git", "config", "--file", str(root / ".gitmodules"), "--get-regexp", r"^submodule\..*\.path$"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise BenchmarkError(f"could not read {root / '.gitmodules'}")
    names = sorted(line.split(" ", 1)[1] for line in out.stdout.splitlines() if " " in line)
    if not names:
        raise BenchmarkError(f"no submodules in {root / '.gitmodules'}")
    return names


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
        # `/` returns the right operand when it is absolute, so this covers
        # both the absolute and repo-relative cases.
        inputs.append(Input(entry["name"], root / Path(entry["path"]).expanduser()))
    if not inputs:
        raise BenchmarkError(f"{path}: no [[input]] entries")
    return inputs


def build(root: Path, names: list[str]) -> None:
    """Refresh the `result-hbt-*` symlinks from each implementation's flake."""
    for name in names:
        print(f"building {name} ...", file=sys.stderr)
        subprocess.run(["nix", "build", f"./{name}#", "-o", f"result-{name}"], cwd=root, check=True)


def hyperfine_version() -> str | None:
    """Which hyperfine produced the timings, for the same reason as the rest."""
    out = subprocess.run(hyperfine_cmd() + ["--version"], capture_output=True, text=True, check=False)
    return out.stdout.strip() or None if out.returncode == 0 else None


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
            reason = f"no {binary} (nix build ./{name}# -o {link.name})"
            print(f"{name}: {reason}", file=sys.stderr)
            impls.append(Impl(name, error=reason))
            continue
        store = str(binary.resolve().parent.parent)
        impls.append(Impl(name, binary, store, self_reported_version(binary), revisions.get(name)))
    return impls


def verify(impls: list[Impl], inputs: list[Input]) -> list[Pair]:
    """Run --info once per pair, for the entity count and to prune what fails."""
    pairs: list[Pair] = []
    runnable = [i for i in impls if i.available]
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


def benchmark(pairs: list[Pair], inputs: list[Input], warmup: int, min_runs: int | None) -> None:
    """Time each input across every implementation that handled it."""
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
            # report, and `hbt-bench > report.md` stays clean.
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


def collect(impls: list[Impl], inputs: list[Input], pairs: list[Pair]) -> dict[str, Any]:
    """Assemble the serializable results document."""
    return {
        "version": FORMAT_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hyperfine": hyperfine_version(),
        "host": {
            "node": platform.node(),
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "implementations": [i.serialize() for i in impls],
        "inputs": [{"name": i.name, "path": str(i.path)} for i in inputs],
        "results": [p.serialize() for p in pairs],
    }
