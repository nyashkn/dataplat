---
paths:
  - "src/**/pipelines/**/*.py"
---

# Pipelines (the only layer that writes)

- One `@DBOS.step` = compute + **one** `Writer` call (one DuckLake transaction). Never two commits in
  one step, and never a write outside a step.
- Run workflows under `SetWorkflowID(workflow_id("<pipeline>", key))`, so re-running is safe. Use
  `attempt="r2"` to recompute on purpose.
- Pass Hamilton outputs as function objects: `run_dag(mod, final=[mod.node], ...)`.
- Commit exactly the contract (`EVENTS = tables.X.with_checks(...)`); the writer validates again.
- Empty partitions are refused. If the source really had no rows, pass `allow_empty=True` and a `note`
  saying why.
- Schedules live in `pipelines/schedules.py` (applied by the worker with `DBOS.apply_schedules`). A
  scheduled workflow takes `(scheduled_time, context)` and starts the real workflow under its
  deterministic id. Order schedules by data dependency, not clock time: a job that needs an upstream
  partition checks for that partition's run-log row.
- Backfills replay schedules (`just cli backfill --schedule ... --start ... --end ...`), never ad-hoc loops.
