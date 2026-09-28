# 0006. A namespace per worktree

- Status: accepted

## Decision
Isolation unit = namespace, resolved from `DATAPLAT_NS`, else the linked git worktree's name, else
`main`. Each namespace has its own DuckLake metadata schema (Postgres) or catalog file (DuckDB), data
prefix (`_ns/<ns>/`), DBOS app name, queue prefix and, on Postgres, DBOS system schema. A linked
worktree that resolves to `main` raises `IsolationError`. `just worktree x` creates the worktree,
branch and namespace in one step.

## Evidence
Two namespaces on one Postgres catalog see only their own rows (`test_postgres_catalog.py`). A
worktree run writes only its namespace; `DATAPLAT_NS=main` inside it is refused.
