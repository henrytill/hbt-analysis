#!/usr/bin/env bash
#
# Commit whatever scripts/update-submodules.sh staged, on a fresh branch, and
# open a pull request for it. Does nothing if the index is clean.
#
# Split out from update-submodules.sh so that advancing pointers stays usable
# by hand -- running it locally should leave the changes staged for review, not
# commit and push them. This is the half only CI wants.
#
# Usage:
#   scripts/open-submodule-pr.sh [summary-file]
#
# The summary file, if given, becomes the commit message body; pass the output
# of update-submodules.sh. Requires the gh CLI with contents and pull-requests
# write access; the built-in GITHUB_TOKEN is enough.

set -euo pipefail

summary=${1-}

cd "$(git rev-parse --show-toplevel)"

if git diff --cached --quiet; then
    echo "nothing staged, no pull request to open"
    exit 0
fi

branch="update-submodules/$(date -u +%Y-%m-%d)"

if [ -n "$summary" ] && [ -r "$summary" ]; then
    body=$(cat "$summary")
else
    body=""
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git checkout -q -b "$branch"
git commit -q -m "Advance submodule pointers" -m "$body"
git push -q origin "$branch"
gh pr create --fill --head "$branch" || echo "a pull request already exists"
