# dataplat: the library + the project template. Same recipes as the projects it renders.
# Install just once per machine: `uv tool install rust-just` (or prefix: `uv run just ...`).
set shell := ["bash", "-euo", "pipefail", "-c"]

run := "uv run --frozen"

# list recipes
default:
    @{{ just_executable() }} --list --unsorted

# install deps (all extras) and git hooks
setup:
    uv sync --all-extras
    {{ run }} pre-commit install -t pre-commit

# ruff
lint:
    {{ run }} ruff format --check .
    {{ run }} ruff check .

# dataplat's own layer contracts
imports:
    {{ run }} lint-imports

# mypy
typecheck:
    {{ run }} mypy

# library tests (Postgres catalog via pgserver, graph via real-ladybug, multi-process DBOS)
test:
    {{ run }} pytest -q

# render the template (with and without the example) and run each project's `just ci`
template-check:
    scripts/check_template.sh

# regenerate the code-derived facts in the docs (Cog)
docs:
    {{ run }} cog -r -I scripts README.md

# fail if a Cog-generated span was edited by hand or is stale
docs-check:
    {{ run }} cog --check -I scripts README.md

# everything CI runs
ci: lint imports docs-check typecheck test template-check
