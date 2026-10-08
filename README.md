# dataplat

A platform library and a project template for data projects that coding agents build and operate.
Humans own the boundaries; the boundaries are code.

- **Library** (`src/dataplat/`): one lake writer, snapshot-pinned readers, table contracts, checks with
  live-fire proof, a census, an answer gate, gated actions, a parity-checked graph, and namespaces for
  parallel agents.
- **Template** (`template/`, via [copier](https://copier.readthedocs.io)): a project skeleton with the
  full config set (ruff, import-linter, jscpd ratchet, pre-commit, justfile, GitHub CI, Claude Code
  settings, hooks, skills and rules, ZeroClaw-format SOPs) and a worked example.

## Start a project
```bash
uv tool install rust-just                                     # once per machine: puts `just` on PATH
uvx copier copy --trust gh:nyashkn/dataplat my-project      # answer the questions
cd my-project && git init -b main && cp .env.example .env
just bootstrap                                                # lockfile, deps, hooks, contracts, formatting
git add -A && git commit -m "Render from dataplat" --no-verify
just ci                                                       # the same gates CI runs
```
Tasks run through [just](https://github.com/casey/just). Each project also pins it in its dev
dependencies; that copy is what CI and the git hooks call (`uv run --frozen just ...`), and it works
without the global install: `uv run just <recipe>`.
Pull convention updates later: `uvx copier update --trust`. Existing repo? See
[docs/adopt-existing.md](docs/adopt-existing.md).

## The stack

| Concern | Choice |
|---|---|
| Storage | DuckLake (Postgres catalog, Parquet on RustFS) through `dataplat.lakecore` |
| Transforms | Apache Hamilton over polars; pandera validation from contracts |
| Contracts | LinkML → `TableContract` constants, Pydantic, JSON Schema |
| Orchestration | DBOS Transact (durable workflows, schedules, approvals) |
| Metrics | boring-semantic-layer behind `dataplat.gate` |
| Graph | LadybugDB native files, rebuilt nightly, published only on SQL/Cypher parity |
| Agents | Claude Code (settings, hooks, skills, path rules, subagent), ZeroClaw SOPs |
| Telemetry | OpenTelemetry from DBOS through scrubbing exporters to [OpenObserve](https://github.com/openobserve/openobserve); alerts as code |

## Library surface

```python
from dataplat.lakecore import connect  # read side, anywhere
from dataplat.lakecore.write import Writer  # write side, <pkg>.pipelines only
from dataplat.lakecore.publish import PublishTarget, publish_bytes, publish_documents  # fixed-key documents, write-once
from dataplat.checks import check, Verdict, Evidence, dirty_expr, measure_dirt, verify_repair, Rate
from dataplat.checks import livefire  # assert_catches(check, clean, mutation)
from dataplat.checks.hamilton import PanderaPolars  # Hamilton @check_output_custom validator
from dataplat.runtime import run_dag, dbos_config, launch, workflow_id
from dataplat.trust import census, deep_census
from dataplat.gate import answer, partitions_complete, fresh_within, min_rows
from dataplat.actions import action, propose, approve, audit
from dataplat.graph import GraphSpec, NodeSpec, RelSpec, Canary, publish, open_current
from dataplat.observe import scrub, error_signature  # telemetry is configured by launch() from the env
from dataplat.observe.alerts import standard_alerts
from dataplat.observe.openobserve import OpenObserve, apply
```

| Guarantee | How |
|---|---|
| A partition exists iff the run log says so | data + run-log row + commit message in one DuckLake transaction |
| Re-runs never double-count | partition overwrite + deterministic DBOS workflow ids |
| Parallel writers can't corrupt a partition | DuckLake conflict detection + bounded retry |
| Readers see one consistent state | `SNAPSHOT_VERSION`-pinned attach per reader |
| Empty partitions are deliberate | refused without `allow_empty` + a note |
| Repairs are reversible | witness column + `pre_snapshot` + `restore_partition` |
| Answers are traceable, or refused | `gate.answer`: preconditions → `Answer` (snapshot, datasets, evidence) or `Refusal` |
| Every check is proven | `pytest --livefire-strict` fails on registered checks never shown catching a bad row |
| No silent graph answers | build → SQL/Cypher parity on canaries → atomic publish |
| Agents in worktrees can't touch `main` | namespace per worktree; `main` refused inside a linked worktree |
| Telemetry can't leak identifiers | spans and logs leave through scrubbing exporters, live-fired against a failing workflow |
| Failures and missed runs reach a human | alerts as code: failure groups, new error signatures, a heartbeat per schedule |

## Develop dataplat
```bash
just setup
just ci        # lint, import contracts, mypy, tests, then render both template variants and run their just ci
```
Tests cover the Postgres catalog (via [pgserver](https://pypi.org/project/pgserver/)), native graph
builds (real-ladybug), and the multi-process worker/CLI action flow.

## Reference (generated from the code by `just docs`)
<!-- [[[cog
import cog, docfacts as f
cog.outl(f"The architecture scanner enforces {len(f.dpa_rules())} rules: {f.code_list(f.dpa_rules())}.")
]]] -->
The architecture scanner enforces 8 rules: `DPA001`, `DPA002`, `DPA003`, `DPA004`, `DPA005`, `DPA006`, `DPA007`, `DPA008`.
<!-- [[[end]]] -->
<!-- [[[cog
cog.outl(f"The `dataplat` CLI has {len(f.cli_subcommands())} subcommands: {f.code_list(f.cli_subcommands())}.")
]]] -->
The `dataplat` CLI has 3 subcommands: `contracts`, `lint`, `ns`.
<!-- [[[end]]] -->
<!-- [[[cog
cog.outl(f"Optional extras: {f.code_list(f.extras())}.")
]]] -->
Optional extras: `contracts`, `graph`, `semantic`, `telemetry`.
<!-- [[[end]]] -->
<!-- [[[cog
cog.outl(f"Recipes in this repo's justfile: {f.code_list(f.just_recipes())}.")
]]] -->
Recipes in this repo's justfile: `ci`, `default`, `docs`, `docs-check`, `imports`, `lint`, `setup`, `template-check`, `test`, `typecheck`.
<!-- [[[end]]] -->

## Decisions
[docs/adr/](docs/adr/): single writer on DuckLake · DBOS · LinkML contracts · lint the architecture,
not the craft · native graph + parity (Icebug findings) · namespaces · extension wheels · distribution
(**pending your decision**) · telemetry to OpenObserve · publish primitive for fixed-key documents.
