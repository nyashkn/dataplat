#!/usr/bin/env bash
# Render the template (with and without the worked example) against this checkout and run each
# rendered project's full `just ci`. The template is only as good as the projects it produces.
set -euo pipefail
unset VIRTUAL_ENV   # each rendered project uses its own .venv
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

render() {  # name example package
  uvx --from "copier>=9.18" copier copy --trust --defaults --vcs-ref HEAD --quiet \
    --data project_name="Check $1" --data package_name="$3" --data cli_name="$3" \
    --data dataplat_source=path --data dataplat_path="$root" \
    --data 'forbidden_columns=["national_id"]' --data include_example="$2" \
    "$root" "$work/$1"
  cd "$work/$1"
  git init -q -b main
  cp .env.example .env
  git add -A && git -c user.email=ci@local -c user.name=ci commit -q -m render --no-verify
  uv run just bootstrap >/dev/null   # first run: creates uv.lock, installs just itself
  # bootstrap's formatter must find nothing to fix in what the template rendered
  if [ -n "$(git diff --name-only)" ]; then
    echo "bootstrap reformatted rendered files; fix them in the template:" >&2
    git diff --stat >&2
    exit 1
  fi
  git add -A && git -c user.email=ci@local -c user.name=ci commit -q -m bootstrap --no-verify
  uv run --frozen just ci
  cd "$root"
}

render example true mdundo_check
render blank false blank_check
echo "template check: both renders pass just ci"
