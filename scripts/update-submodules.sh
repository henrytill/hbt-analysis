#!/usr/bin/env bash
#
# Advance each submodule pointer to the head of its remote's default branch.
#
# This repo's pointers are bumped by hand and go stale, which makes a failing
# golden test easy to misread as a parser bug. See AGENTS.md.
#
# Deliberately not `git submodule update --remote`: that resolves the branch
# from submodule.<name>.branch in .gitmodules, which is unset here, and is
# further confused by leftover local branches inside the submodule checkouts.
# This asks each remote what its default branch is instead.
#
# Only the top-level submodules are touched. The nested hbt-data pointers
# belong to the implementations and are bumped in their own repos.
#
# Each submodule is also a flake input of the same name, locked in flake.lock,
# and .#bench builds the locked revision rather than the gitlink. So a run that
# advances a pointer re-locks that input too, which needs nix on PATH.
#
# Usage:
#   scripts/update-submodules.sh [-n|--dry-run]

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

changed=0
failed=0
advanced=()

while read -r path; do
	[ -n "$path" ] || continue

	old=$(git -C "$path" rev-parse HEAD)

	branch=$(git -C "$path" ls-remote --symref origin HEAD \
		| awk '$1 == "ref:" { sub("refs/heads/", "", $2); print $2; exit }')
	if [ -z "$branch" ]; then
		printf '%s: cannot determine default branch, skipped\n' "$path" >&2
		failed=$((failed + 1))
		continue
	fi

	git -C "$path" fetch --quiet origin "$branch"
	new=$(git -C "$path" rev-parse FETCH_HEAD)

	if [ "$old" = "$new" ]; then
		printf '%s: %s  already current (%s)\n' "$path" "${old:0:7}" "$branch"
		continue
	fi

	# In a shallow clone the counts are wrong rather than missing: the fetched
	# head can arrive grafted, cut off from the old revision, so a one-commit
	# fast-forward counts as +1 -1 and reads as a rewind. Ask whether the clone
	# is shallow instead of trusting rev-list to fail.
	if [ "$(git -C "$path" rev-parse --is-shallow-repository)" = true ]; then
		detail="shallow clone, commits not counted"
	else
		ahead=$(git -C "$path" rev-list --count "$old..$new")
		behind=$(git -C "$path" rev-list --count "$new..$old")
		if [ "$behind" != 0 ]; then
			detail="+$ahead -$behind commits, NOT a fast-forward"
		else
			detail="+$ahead commits"
		fi
	fi
	printf '%s: %s -> %s  (%s, %s)\n' \
		"$path" "${old:0:7}" "${new:0:7}" "$branch" "$detail"

	if $dry_run; then
		changed=$((changed + 1))
		continue
	fi

	# A submodule left dirty by a local build fails to check out. Report and
	# skip it as the missing-branch case above does, rather than letting set -e
	# abort the run with earlier submodules already staged and no summary.
	if ! git -C "$path" checkout --quiet --detach "$new"; then
		printf '%s: checkout failed, skipped (dirty worktree?)\n' "$path" >&2
		failed=$((failed + 1))
		continue
	fi
	git add "$path"
	advanced+=("$path")
	changed=$((changed + 1))
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | cut -d' ' -f2)

# Each implementation pins its own hbt-data revision. Advancing the outer
# pointer leaves those nested checkouts behind, which shows up as mass golden
# test failures rather than a clear error -- so bring them to what the new
# revisions pin. This checks out their pins; it does not advance them.
if [ "$changed" -gt 0 ] && ! $dry_run; then
	git submodule update --init --recursive --quiet

	# The same omission one layer up: a lock left on the old revisions builds
	# something other than what the gitlinks say. Only the inputs that moved:
	# a skipped submodule's worktree is in whatever state made it fail, which
	# is nothing this run should write into the lock.
	nix flake update "${advanced[@]}"
	git add flake.lock
fi

if [ "$changed" -eq 0 ] && [ "$failed" -eq 0 ]; then
	echo "all submodules current"
elif [ "$changed" -eq 0 ]; then
	echo "no submodules advanced"
elif $dry_run; then
	printf '\n%s submodule(s) would be advanced; re-run without --dry-run\n' "$changed"
else
	printf '\n%s submodule(s) advanced and staged, with flake.lock re-locked to match\n' "$changed"
fi

# Exit non-zero on a partial run so CI does not open a pull request that
# silently covers only some of the submodules.
if [ "$failed" -gt 0 ]; then
	printf '%s submodule(s) could not be updated\n' "$failed" >&2
	exit 1
fi
