# Repair a derived column

Only columns with a witness (for example `phone_number` from `phone_raw`) can be repaired in place.
No witness means no in-place repair: fix the transform and backfill instead.

## Steps

1. **Measure** — Quantify the problem at a pinned snapshot: `dirty_expr(column, cleaner)` counts and
   shape classes per partition. Record the snapshot id.
   - tools: shell
2. **Fix the cleaner first** — The repair uses the same expression the pipeline uses. Change it in
   `transforms/`, add a live-fire test for the case you found, and pass `just ci`.
   - tools: shell
   - on_failure: fail
3. **Rehearse** — In a worktree namespace, call `Writer.repair_partition(contract, day, column=...,
   witness=..., derive=<cleaner>, reason=<issue>, run_id=...)` on one partition. The writer verifies:
   same rows, witness untouched, other columns untouched, derivation holds.
   - tools: shell
4. **Approval** — Show the owner the per-partition `changed` counts and the `pre_snapshot` ids.
   - kind: approval
   - requires_confirmation: true
5. **Apply and record** — The owner applies it per partition. Each receipt records `pre_snapshot`;
   `Writer.restore_partition(..., snapshot=pre_snapshot)` undoes it exactly.
   - tools: shell
   - terminal: true
