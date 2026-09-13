"""The implementations: which there are, which a run names, and where their binaries are.

What every command needs before it can run anything, and nothing either
library knows: `hbt.bench` and `hbt.conformance` are both handed binaries, and
this is where they come from.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hbt.bench import reported_version


class CommandError(Exception):
    """A condition that should stop a command with a message, not a traceback.

    Every command's: `hbt.analysis.commands.invoke` turns it into exit status 2
    for all of them, so a command refuses by raising this, and translates a
    library's own error -- `hbt.bench.BenchmarkError`,
    `hbt.conformance.CorpusError` -- into it.
    """


@dataclass
class Impl:
    """One implementation, whether or not a binary for it was found.

    Satisfies :class:`hbt.bench.Implementation`, so a benchmark can be handed
    these directly.
    """

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


@dataclass(frozen=True)
class Selection:
    """The implementations a run is about, as the command line named them.

    A record of its own rather than fields repeated in each command's options,
    so that a command's `run` takes the same thing whichever command it is,
    and the flake's apps can hand every command the same arguments.
    """

    impl: tuple[str, ...] = ()
    binary: tuple[str, ...] = ()
    revision: tuple[str, ...] = ()

    @classmethod
    def take(cls, kwargs: dict[str, Any]) -> Selection:
        """Build one from a command's keyword arguments, removing the fields it used."""
        return cls(**{f.name: kwargs.pop(f.name) for f in dataclasses.fields(cls)})


def submodules(gitmodules: Path) -> dict[str, str]:
    """Each submodule path in `gitmodules`, mapped to its URL.

    Read with `git config` rather than parsed here, the way both scripts read
    it too.
    """
    out = subprocess.run(
        ["git", "config", "--file", str(gitmodules), "--get-regexp", r"^submodule\..*\.(path|url)$"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise CommandError(f"could not read {gitmodules}")
    sections: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition(" ")
        section, _, attribute = key.rpartition(".")
        sections.setdefault(section, {})[attribute] = value
    return {s["path"]: s.get("url", "") for s in sections.values() if "path" in s}


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
    names = sorted(submodules(root / ".gitmodules"))
    if not names:
        raise CommandError(f"no submodules in {root / '.gitmodules'}")
    return names


def repo_root() -> Path:
    """The top of the working tree this is being run from."""
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def parse_pairs(specs: tuple[str, ...], flag: str, shape: str, known: list[str]) -> dict[str, str]:
    """Parse repeated NAME=VALUE arguments, validating NAME."""
    parsed: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or name not in known:
            raise CommandError(f"{flag} expects {shape} with a known NAME, got {spec!r}")
        parsed[name] = value
    return parsed


def choose(root: Path, selection: Selection) -> tuple[list[str], list[str], dict[str, Path], dict[str, str]]:
    """The known implementations, the ones a run names, and its overrides, validated."""
    known = implementations(root)
    names = list(selection.impl or known)
    unknown = set(names) - set(known)
    if unknown:
        raise CommandError(f"unknown implementation(s): {', '.join(sorted(unknown))}")
    overrides = {n: Path(v) for n, v in parse_pairs(selection.binary, "--binary", "NAME=PATH", known).items()}
    revisions = parse_pairs(selection.revision, "--revision", "NAME=REV", known)
    return known, names, overrides, revisions


def build(root: Path, names: list[str]) -> None:
    """Refresh the `result-hbt-*` symlinks from each implementation's flake."""
    for name in names:
        print(f"building {name} ...", file=sys.stderr)
        subprocess.run(["nix", "build", f"./{name}#", "-o", f"result-{name}"], cwd=root, check=True)


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
        impls.append(Impl(name, binary, store, reported_version([str(binary)]), revisions.get(name)))
    return impls
