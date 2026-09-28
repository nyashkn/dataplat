# 0001. One writer, DuckLake transactions, run log in the same commit

- Status: accepted

## Context
mdundo-pipeline wrote Parquet from seven places, each with its own S3 helpers (`_split_s3`,
`_make_s3_fs`, `_move_or_copy`, ...). A partition's existence was inferred by listing RustFS, which
truncates at about 501 entries. There was no record of which run produced which partition.

## Decision
All lake writes go through `dataplat.lakecore.write.Writer` on DuckLake (Postgres catalog, Parquet on
RustFS). Every write is one DuckLake transaction containing the data change, a `_meta.dataset_runs` row
and a commit message carrying the run id. Partition overwrite is DELETE + INSERT in that transaction.
Files are located through the catalog, never by listing.

## Evidence (probed with duckdb 1.5.5 + ducklake 1.5.5)
- A multi-table transaction produces one snapshot; ROLLBACK leaves nothing.
- Concurrent overwrite of one partition: the second COMMIT fails with a conflict and the final state
  holds one writer's rows, never a mix (`tests/integration/test_lake.py`).
- `ducklake_add_data_files` adopts hive-partitioned legacy files in place.
- `set_commit_message(..., extra_info => json)` works inside the transaction.
- `ATTACH ... (SNAPSHOT_VERSION n)` gives a pinned, read-only view.

## Consequences
Enforced by ruff bans and `dataplat lint` DPA001–DPA004, and checked on the box with
`deep_census` (run log vs. table counts).
