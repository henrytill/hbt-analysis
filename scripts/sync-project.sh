#!/usr/bin/env bash
#
# Add any issue from the hbt repos that is missing from the MBGA project board.
#
# Reconciles rather than reacts: re-adds anything missed while it was not
# running, and is safe to repeat. Adding an item already on the board is a
# no-op. Only issues are added; pull requests and existing items are untouched.
#
# Usage:
#   scripts/sync-project.sh [-n|--dry-run]
#
# Requires the gh CLI, authenticated with `project` (write) and `public_repo`.
# In CI that means a PAT: the built-in GITHUB_TOKEN is scoped to one repository
# and cannot write a user-owned Projects v2 board.

set -euo pipefail

OWNER=${OWNER:-henrytill}
PROJECT=${PROJECT:-1}
REPOS=${REPOS:-"hbt-hs hbt-go hbt-ocaml hbt-rs hbt-data"}

dry_run=false
case ${1-} in
    -n | --dry-run) dry_run=true ;;
    "") ;;
    *)
        printf 'usage: %s [-n|--dry-run]\n' "$0" >&2
        exit 2
        ;;
esac

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

gh project item-list "$PROJECT" --owner "$OWNER" --limit 1000 --format json \
    | jq -r '.items[].content.url // empty' \
    | sort -u > "$workdir/on-board"

for repo in $REPOS; do
    gh issue list -R "$OWNER/$repo" --state all --limit 1000 --json url \
        | jq -r '.[].url'
done | sort -u > "$workdir/wanted"

comm -23 "$workdir/wanted" "$workdir/on-board" > "$workdir/missing"

printf 'on board: %s   issues: %s   missing: %s\n' \
    "$(wc -l < "$workdir/on-board")" \
    "$(wc -l < "$workdir/wanted")" \
    "$(wc -l < "$workdir/missing")"

if [ ! -s "$workdir/missing" ]; then
    echo "nothing to do"
    exit 0
fi

while read -r url; do
    if $dry_run; then
        printf 'would add %s\n' "$url"
    else
        printf 'adding %s\n' "$url"
        gh project item-add "$PROJECT" --owner "$OWNER" --url "$url" > /dev/null
    fi
done < "$workdir/missing"
