# Conventions

Every rule has a **tier**:
- **auto**: fixed for you (formatter, autofix). Never discuss these in review.
- **enforced**: a tool fails the build, and a live-fire test proves the tool catches it.
- **judgment**: no tool can check it. Reviewers (human or agent) look for it.

The **origin** says why the rule exists. Rules without an incident behind them do not belong here.

## The rules

| # | Rule | Tier | Enforced by | Origin |
|---|---|---|---|---|
| 1 | Formatting, import order, modern syntax | auto | ruff format / `ruff --fix`, post-edit hook | review time spent on style |
| 2 | Only `pipelines/` writes the lake, through `Writer` | enforced | ruff TID251 (`pyarrow.parquet`), DPA001 (file writes), DPA002 (`COPY … TO`), DPA004 (writer import), DPA007 (mutating SQL outside pipelines) | seven hand-rolled Parquet writers drifted apart |
| 3 | Never list storage; ask the catalog or use a manifest | enforced | TID251 (`os.listdir`, `glob`), DPA003 | RustFS truncates listings at about 501 entries |
| 4 | Layers: cli > pipelines\|actions > semantic\|graph > transforms\|sources > contracts | enforced | import-linter `layers` | cycles made changes ripple everywhere |
| 5 | Transforms are pure (no I/O, env, duckdb, dbos, pandas) | enforced | import-linter `pure-transforms`, DPA005 (lake), DPA008 (`open`, `os.environ`, `pl.read_*`, `.read_text`) | untestable transforms |
| 6 | No string dispatch (`import_module(name)`, name-string Hamilton outputs) | enforced | TID251, DPA006, `run_dag` TypeError | `usage.recon` string dispatch fooled two audits and desloppify (99 of 146 live files flagged dead) |
| 7 | Tables are referenced by generated constants, not name strings | judgment | constants are generated from LinkML; code search finds every use | renamed tables broke silently |
| 8 | Generated contracts are never stale | enforced | `dataplat contracts check` (pre-commit, CI) | drift between YAML and code |
| 9 | Frames match their contract exactly; nothing is coerced | enforced | `TableContract.validate` at transform output and at commit | silent type coercion |
| 10 | Every registered check is live-fired | enforced | `pytest --livefire-strict` (test-all, CI) | V6/V21/V24 existed, but production never called them |
| 11 | No new duplicated code | enforced | jscpd ratchet vs main (`--fail-on-new-clones`) + MCP at write time | duplicated S3 helpers (`_split_s3`, `_make_s3_fs`, …) |
| 12 | Empty partitions need a reason | enforced | writer refuses 0 rows without `allow_empty` + note | 20 zero-row days in `fact_charges_topline`, no writer |
| 13 | Restricted fields never enter the lake | enforced | `forbidden_columns` refused at `just contracts` | `national_id`, `payout_number`, `balance` in a source table |
| 14 | Secrets are never read or printed by agents | enforced | `.claude/settings.json` deny rules + `guard_bash.py` (env/printenv/export/set as a command word anywhere, `.env*`, secret CLIs), gitleaks | env dumps in agent transcripts |
| 15 | No commits to main; no skipped hooks | enforced | `no-commit-to-branch`, guard hook (`--no-verify`, `-n`, `SKIP=`, `core.hooksPath`) | unreviewed agent changes |
| 16 | Datetimes are timezone-aware; partitions are UTC | enforced | ruff DTZ, every lake session sets `TimeZone=UTC` | partition dates moving with the box's timezone |
| 17 | Worktrees never write the main namespace | enforced | `dataplat.isolation` refuses `main` in a linked worktree | parallel agents overwriting each other |
| 18 | Guardrail config changes need the owner | enforced | CODEOWNERS + `ask` permissions | agents loosening a rule to pass it |
| 19 | Search before writing; extend over duplicate | judgment | (jscpd catches the leftovers) | seven writers, six validators |
| 20 | Claims say *measured* (command + snapshot) or *implied* (code) | judgment | PR template evidence section | audits that "confirmed" what code implied |
| 21 | Counts, rates and shape classes only; no raw identifiers in output, logs or files | judgment | `shape_counts`, Evidence types make it easy | PII exposure risk in agent transcripts |
| 22 | Inconclusive is an answer; blocked metrics are NULL, never 0 | judgment (+ gate) | `dataplat.gate` preconditions, `Rate.value` | fake zeros read as "nothing happened" |
| 23 | Know the grain before any SUM; ratios via `Rate` | judgment | contracts declare `dataplat.grain` | summing quote revisions turned $11k into $567k |
| 24 | Repairs recompute from an untouched witness column | enforced for `repair_partition` | writer refuses repairs without a witness | irreversible in-place fixes |
| 25 | Thresholds are named constants with an origin comment | judgment | — | magic numbers nobody could justify |
| 26 | Schedules follow data dependencies, not the clock | judgment (+ example) | run-log preconditions; `pipelines/publish_graph.py` waits for its upstream partition | cron ordering by clock (#115) |
| 27 | Data checks run on the box, never in CI | enforced | `verify-prod` requires `DATAPLAT_ON_BOX=1`; `tests/prod` is neither collected nor selected elsewhere | lake is tailnet-only |
| 28 | Humans approve actions; agents only propose | enforced (agent side) | guard hook + deny rules on `approve`; `--by` names the approver in the audit | agents approving their own proposals |
| 29 | Telemetry leaves the process scrubbed; messages still hold counts and shapes only | enforced (backstop) | dataplat's scrubbing exporters, live-fired against a failing workflow in dataplat's tests | exception messages quoting phone numbers |
| 30 | What should wake someone is code, reviewed like code | enforced | `just alerts` derives failures, new errors and heartbeats from DBOS spans and `SCHEDULES`; `--apply` is the owner's | alerts clicked together in a UI, then lost |

## Exempting one line

`# dataplat: allow[DPA003] -- reason` (the reason is required, DPA000). For ruff: `# noqa: TID251`
with a comment explaining why. Every exemption shows up in `git grep "allow\[\|noqa"`; the owner reviews
them.

## Adding a rule

When the same mistake shows up twice in `docs/friction-log.md`:
1. Make it a tool check (ruff ban, import contract, DPA rule, contract annotation), or else write it down
   here as *judgment*.
2. Add a live-fire test in `tests/architecture/test_gates.py` that plants the mistake and shows the gate
   catches it.
3. Add the row here, with its origin.
