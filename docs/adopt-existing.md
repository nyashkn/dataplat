# Adopting dataplat in an existing repo (worked example: dockblocks-data-ops)

A new project starts from `copier copy`. An existing one adopts the conventions in ratchets: every step
leaves CI green, and later steps tighten what earlier ones allowed.

## 0. Render a reference copy
```bash
uvx copier copy --trust --data include_example=false --data package_name=dockblocks gh:nyashkn/dataplat /tmp/db-ref
```
Take files from it; don't render over the live repo.

## 1. Guardrails that pass on day one
| Take | Adjust for Dockblocks |
|---|---|
| `ruff.toml` | merge the `select` list and banned APIs into the existing config; per-file-ignore TID251 on the legacy writers for now |
| `justfile` | the recipe names are the interface agents, hooks and CI call. Point the recipes at the existing commands; a Makefile can stay as a thin wrapper for people who type `make` |
| `.jscpd.json` + `just dupes` | the ratchet starts clean: existing clones are the baseline, only new ones fail |
| `.pre-commit-config.yaml` | as is (ruff, gitleaks, no-commit-to-branch, import contracts) |
| `.claude/settings.json`, `.claude/hooks/*` | as is (it covers Infisical); add patterns for Signet's secret-reading commands to `guard_bash.py` |
| CI `gates` job | `just lint imports typecheck test-all dupes` |

## 2. Map directories to layers
Write `.importlinter` layers over the existing packages: Dagster definitions → `pipelines`, extractors →
`sources`, pure transforms → `transforms`, Rill-facing SQL/metrics → `semantic`. Add only contracts that
pass today. Tighten one per PR as code moves.

## 3. One writer, gradually
Run `dataplat lint`. Each existing writer gets `# dataplat: allow[DPA001] -- migrating to Writer (#issue)`.
Migrate one dataset at a time to `Writer.commit_partition` inside its Dagster asset (one transaction
per partition), then delete the allow comment. `git grep "allow\["` is the migration backlog.

**Dagster stays.** `dataplat.runtime` (DBOS) is optional; lakecore, checks, contracts, gate, trust and
graph don't depend on it. An asset body becomes: extract (sources) → transform → `Writer.commit_partition`.

## 4. Adopt bronze history without rewriting
`bronze/zoho/{module}/extracted_date=YYYY-MM-DD/part-0000.parquet` is hive layout, which is exactly what
`Writer.register_files` adopts in place. Build the manifest from the extractor's own records (never a
RustFS listing), then register per module with `expected_rows`.

## 5. Metrics: one definition each
Rill metrics views already play the semantic-layer role. Keep the DRY law (a measure lives in one
metrics view) and add `dataplat.grain` to every contract. The quote-revision grain trap ($11k → $567k)
came from this repo. Questions agents ask go through `dataplat.gate.answer` with preconditions.

## 6. Agent context: progressive disclosure
- AGENTS.md: commands, layers, the enforced list, the judgment list (use the template's as a base),
  keeping the gitnexus block between its markers.
- Move the 47k-character Rill conventions file to `.claude/rules/rill.md` with `paths: ["rill/**"]`, so
  it loads only when an agent works on Rill files.
- Keep `friction_log.md`; recurring entries become rules (template `docs/CONVENTIONS.md`, "Adding a rule").
- semcheck stays advisory (not a gate).

## 7. Parallel agents
Adopt `dataplat.isolation` with the lake connection: worktrees get their own DuckLake metadata schema
and data prefix. Dagster runs from worktrees need their own `DAGSTER_HOME` (set it per worktree in
`scripts/worktree_new.sh`).
