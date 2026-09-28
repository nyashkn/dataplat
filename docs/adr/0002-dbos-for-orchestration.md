# 0002. DBOS Transact for orchestration

- Status: accepted

## Context
Dockblocks uses Dagster, which is asset-centric and heavy for this box. Mdundo needs plain-Python
pipelines, durable retries, schedules, and human-in-the-loop approvals for agent actions (the A&R agent).

## Decision
DBOS Transact. Workflows and steps are decorated Python functions with Postgres-backed state (SQLite in
dev). A step is compute + one lake transaction, so retries are safe. Workflow ids are deterministic
(`workflow_id(pipeline, key)`), so repeated runs are no-ops. Approvals use `DBOS.recv`/`send`. Schedules
are declared (`DBOS.apply_schedules`) and replayed with `backfill_schedule`, which uses the same
deterministic ids.

Process roles: one long-lived **worker** (stable executor id, listens to queues, recovers its own
work) and short-lived **cli** processes (ephemeral executor id, listen to no queues). Agents propose
actions from cli processes; the worker executes them.

## Evidence
- Same workflow id: the step body runs once; replay returns the recorded result.
- `recv`/`send` approval survives across processes (`test_worker_executes_what_a_cli_process_proposes`).
- DBOS 3.x removed `@DBOS.scheduled`; schedules are data (`ScheduleInput`), which is also how
  backfills work.

## Consequences
Dagster's asset UI is gone; lineage comes from Hamilton, run history from the run log and DBOS. A
Dagster project can still adopt everything except `dataplat.runtime` (see docs/adopt-existing.md).
