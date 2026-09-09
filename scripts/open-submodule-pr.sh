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
#   scripts/open-submodule-pr.sh <summary-file>
#
# The summary file becomes the commit message body; pass the output of
# update-submodules.sh. Requires the gh CLI with contents and pull-requests
# write access; the built-in GITHUB_TOKEN is enough. Refuses to run outside
# GitHub Actions: it commits, pushes, and leaves you on a new branch.

set -euo pipefail

summary=${1-}

if [ "${GITHUB_ACTIONS-}" != "true" ]; then
	printf '%s: refusing to run outside GitHub Actions\n' "$0" >&2
	exit 2
fi

cd "$(git rev-parse --show-toplevel)"

if git diff --cached --quiet; then
	echo "nothing staged, no pull request to open"
	exit 0
fi

# The commit message claims the pointers moved, so make sure that is all that
# is staged rather than sweeping up whatever else happened to be in the index.
unexpected=$(git diff --cached --name-only \
	| grep -vxF -f <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | cut -d' ' -f2) \
	|| true)
if [ -n "$unexpected" ]; then
	printf '%s: staged paths that are not submodules:\n%s\n' "$0" "$unexpected" >&2
	exit 2
fi

# The date alone collides on a same-day rerun -- a workflow_dispatch retry,
# or a dispatch on the 1st when the cron also fires. The push would be
# rejected as non-fast-forward before the already-exists check below could
# help, so give each run its own branch.
branch="update-submodules/$(date -u +%Y-%m-%d-%H%M%S)"

# An unreadable summary is an error, not an empty body: the list of what
# moved is the whole point of the pull request.
if [ -z "$summary" ]; then
	printf '%s: no summary file given\n' "$0" >&2
	exit 2
elif [ ! -s "$summary" ]; then
	printf '%s: summary file %s is missing or empty\n' "$0" "$summary" >&2
	exit 2
fi
body=$(cat "$summary")

# -c rather than `git config`, which would write the bot identity into the
# clone's local config and author every later commit there as the bot.
git checkout -q -b "$branch"
git \
	-c user.name="github-actions[bot]" \
	-c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
	commit -q -m "Advance submodule pointers" -m "$body"
git push -q origin "$branch"
# Ask whether a pull request exists rather than treating every gh failure as
# a duplicate -- an expired token or an API outage must not report success.
existing=$(gh pr list --head "$branch" --state open --json url --jq '.[0].url // empty')
if [ -n "$existing" ]; then
	printf 'a pull request already exists: %s\n' "$existing"
else
	gh pr create --fill --head "$branch"
fi
