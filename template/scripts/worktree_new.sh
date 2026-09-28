#!/usr/bin/env bash
# Isolated workspace for a parallel agent: a git worktree on its own branch, with its own lake
# namespace (DuckLake metadata schema or catalog file, data prefix, DBOS app name). Nothing done
# there can touch main's partitions. Usage: scripts/worktree_new.sh feat-x   (or: just worktree feat-x)
set -euo pipefail
name="${1:?usage: worktree_new.sh NAME}"
if [[ ! "$name" =~ ^[a-z0-9][a-z0-9-]{0,39}$ || "$name" == main ]]; then
  echo "worktree name: lowercase letters, digits and dashes, at most 40 characters, not 'main'" >&2
  exit 2
fi
root="$(git rev-parse --show-toplevel)"
dir="$root/.trees/$name"
branch="wt/$name"

if [ -d "$dir" ]; then
  echo "exists: $dir" >&2
else
  if git -C "$root" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$root" worktree add "$dir" "$branch"
  else
    git -C "$root" worktree add "$dir" -b "$branch"
  fi
fi
[ -f "$root/.env" ] && [ ! -f "$dir/.env" ] && cp "$root/.env" "$dir/.env"

cd "$dir"
uv run --frozen just setup >/dev/null
env_file=""; [ -f .env ] && env_file="--env-file .env"
# The namespace comes from the worktree directory name; dataplat refuses 'main' inside a worktree.
uv run --frozen $env_file dataplat ns --init
echo "ready: $dir  (branch $branch)"
