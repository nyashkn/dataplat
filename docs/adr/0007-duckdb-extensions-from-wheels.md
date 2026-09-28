# 0007. DuckDB extensions come from pinned PyPI wheels

- Status: accepted

## Context
`INSTALL ducklake` downloads from extensions.duckdb.org at runtime. That host returned 403 under the
build environment's egress policy, and runtime downloads are unpinned in any case.

## Decision
Depend on `duckdb-extension-ducklake`, `-postgres-scanner` and `-httpfs` wheels pinned to the DuckDB
version, and `LOAD` them by path with autoinstall and autoload disabled. uv.lock pins everything.

## Consequences
Upgrading DuckDB means upgrading all four together, in one PR.
