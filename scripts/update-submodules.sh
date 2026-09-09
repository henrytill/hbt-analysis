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
# Only the four top-level submodules are touched. The nested hbt-data pointers
# belong to the implementations and are bumped in their own repos.
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

while read -r path; do
    [ -n "$path" ] || continue

    old=$(git -C "$path" rev-parse HEAD)

    branch=$(git -C "$path" ls-remote --symref origin HEAD \
        | awk '$1 == "ref:" { sub("refs/heads/", "", $2); print $2; exit }')
    if [ -z "$branch" ]; then
        printf '%-10s cannot determine default branch, skipped\n' "$path" >&2
        continue
    fi

    git -C "$path" fetch --quiet origin "$branch"
    new=$(git -C "$path" rev-parse FETCH_HEAD)

    if [ "$old" = "$new" ]; then
        printf '%-10s %s  already current (%s)\n' "$path" "${old:0:7}" "$branch"
        continue
    fi

    count=$(git -C "$path" rev-list --count "$old..$new" 2> /dev/null || echo "?")
    printf '%-10s %s -> %s  (%s, +%s commits)\n' \
        "$path" "${old:0:7}" "${new:0:7}" "$branch" "$count"
    changed=$((changed + 1))

    if ! $dry_run; then
        git -C "$path" checkout --quiet --detach "$new"
        git add "$path"
    fi
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | cut -d' ' -f2)

# Each implementation pins its own hbt-data revision. Advancing the outer
# pointer leaves those nested checkouts behind, which shows up as mass golden
# test failures rather than a clear error -- so bring them to what the new
# revisions pin. This checks out their pins; it does not advance them.
if [ "$changed" -gt 0 ] && ! $dry_run; then
    git submodule update --init --recursive --quiet
fi

if [ "$changed" -eq 0 ]; then
    echo "all submodules current"
elif $dry_run; then
    printf '\n%s submodule(s) would be advanced; re-run without --dry-run\n' "$changed"
else
    printf '\n%s submodule(s) advanced and staged; review and commit\n' "$changed"
fi
