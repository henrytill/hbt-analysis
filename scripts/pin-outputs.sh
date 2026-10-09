#!/usr/bin/env bash
#
# Pin each implementation's CLI in the Cachix cache, under the submodule's
# name, so the cache's garbage collector keeps the builds this repo points at.
#
# What is pinned is the root flake's package of that name: the flake.lock
# revision, which is what .#bench and .#conformance run -- not the working
# tree's checkout and not the result-hbt-* symlinks, either of which can lag.
# Each pin keeps one revision, so pinning a new build releases the previous
# one to the cache's ordinary garbage collection.
#
# Only meant to follow master. The info workflow runs it on pushes there and
# nowhere else: a pull request's revisions pinned under the same names would
# release the ones master points at.
#
# Usage:
#   scripts/pin-outputs.sh [-n|--dry-run]
#
# Requires nix, and cachix authenticated with write access to the henrytill
# cache (CACHIX_AUTH_TOKEN, or `cachix authtoken`). The packages are realised
# first, since cachix pins only paths in the local store; in CI .#bench has
# already built them.

set -euo pipefail

dry_run=false
case ${1-} in
	-n | --dry-run) dry_run=true ;;
	"") ;;
	*)
		printf 'usage: %s [-n|--dry-run]\n' "$0" >&2
		exit 2
		;;
esac

cd "$(git rev-parse --show-toplevel)"

while read -r name; do
	[ -n "$name" ] || continue

	if $dry_run; then
		out=$(nix eval --raw ".#$name.outPath")
		printf '%s: would pin %s\n' "$name" "$out"
		continue
	fi

	out=$(nix build --no-link --print-out-paths ".#$name")
	cachix pin henrytill "$name" "$out" --artifact bin/hbt --keep-revisions 1
	printf '%s: pinned %s\n' "$name" "$out"
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | cut -d' ' -f2)
