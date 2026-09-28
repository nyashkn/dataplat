"""Read side of the lake. Import from anywhere.

``Lake`` is the catalog handle. ``Lake.reader()`` returns a ``Reader`` pinned to one snapshot, so every
query in a session sees the same data even if a pipeline commits halfway through. Answers carry that
snapshot id as provenance.

In reader SQL, name tables as ``schema.table`` (``usage.events_clean``). The reader resolves them at its
snapshot. Writing ``lake.usage.events_clean`` would bypass the pin, so the reader rejects it.

Files are found through the catalog (``Lake.files``), never by listing storage. RustFS truncates
directory listings (about 501 entries), and listing is how a lake silently loses history.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import duckdb
import polars as pl

from dataplat.config import LakeConfig
from dataplat.contracts.model import TableContract
from dataplat.lakecore._connect import ALIAS, acquire, attach_pinned, release

RUNLOG = "_meta.dataset_runs"
_UNPINNED_REF = re.compile(rf'(?i)(?<![\w"])"?{ALIAS}"?\s*\.\s*"?\w+"?\s*\.')

_RUNLOG_EMPTY = pl.Schema(
    {
        "dataset": pl.String(),
        "partition": pl.String(),
        "rows": pl.Int64(),
        "mode": pl.String(),
        "run_id": pl.String(),
        "code_version": pl.String(),
        "committed_at": pl.Datetime("us", "UTC"),
    }
)


class Reader:
    """Read-only, snapshot-pinned view of the lake. Use as a context manager."""

    def __init__(self, cfg: LakeConfig, snapshot: int, *, read_only: bool = False) -> None:
        self.cfg = cfg
        self.snapshot = snapshot
        self.con = acquire(cfg, read_only)
        self.alias = attach_pinned(self.con, cfg, snapshot)

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame:
        if _UNPINNED_REF.search(query):
            raise ValueError(
                f"reader SQL must name tables as schema.table, not {ALIAS}.schema.table: "
                "the reader resolves schema.table at its pinned snapshot"
            )
        return self.con.execute(query, params or []).pl()

    def scan(
        self, contract: TableContract, where: str | None = None, params: Sequence[Any] | None = None
    ) -> pl.DataFrame:
        cols = ", ".join(f'"{c}"' for c in contract.column_names())
        q = f"SELECT {cols} FROM {contract.table}" + (f" WHERE {where}" if where else "")
        if not self.has_table(contract):
            return contract.empty()
        return self.sql(q, params).cast(dict(contract.polars_schema()))  # type: ignore[arg-type]

    def _table_exists(self, schema: str, table: str) -> bool:
        q = "SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND schema_name = ? AND table_name = ?"
        row = self.con.execute(q, [self.alias, schema, table]).fetchone()
        return bool(row and row[0])

    def has_table(self, contract: TableContract) -> bool:
        return self._table_exists(contract.schema_name, contract.table_name)

    def runlog(self) -> pl.DataFrame:
        """Per (dataset, partition) at this snapshot: the latest run, or the sum of all runs for append-only data."""
        if not self._table_exists("_meta", "dataset_runs"):
            return pl.DataFrame(schema=_RUNLOG_EMPTY)
        return self.sql(
            f"""
            SELECT dataset, "partition",
                   CASE WHEN bool_and(mode = 'append') THEN sum("rows")::BIGINT
                        ELSE arg_max("rows", committed_at) END AS "rows",
                   arg_max(mode, committed_at) AS mode, arg_max(run_id, committed_at) AS run_id,
                   arg_max(code_version, committed_at) AS code_version, max(committed_at) AS committed_at
            FROM {RUNLOG}
            GROUP BY ALL
            ORDER BY dataset, "partition"
            """
        )

    def ibis(self) -> Any:
        """An ibis backend over this pinned cursor (for the semantic layer)."""
        import ibis

        return ibis.duckdb.from_connection(self.con)

    def provenance(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot, "namespace": self.cfg.namespace, "code_version": self.cfg.code_version}

    def close(self) -> None:
        release(self.cfg, self.con)

    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Lake:
    """Catalog handle. Writers are built on it; readers pin a snapshot. One per thread.

    Agent-facing processes (Q&A, MCP tools, notebooks) open ``Lake(read_only=True)``. In a process with
    no writer, the attach itself is read-only. See ``_connect.acquire`` for how read-only is shared.
    """

    def __init__(self, cfg: LakeConfig | None = None, *, read_only: bool = False) -> None:
        self.cfg = cfg or LakeConfig.from_env()
        self.read_only = read_only
        self._con: duckdb.DuckDBPyConnection | None = None

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = acquire(self.cfg, self.read_only)
        return self._con

    def current_snapshot(self) -> int:
        row = self.con.execute(f"SELECT id FROM {ALIAS}.current_snapshot()").fetchone()
        assert row is not None
        return int(row[0])

    def reader(self, snapshot: int | None = None) -> Reader:
        snap = self.current_snapshot() if snapshot is None else snapshot
        return Reader(self.cfg, snap, read_only=self.read_only)

    def snapshots(self) -> pl.DataFrame:
        return self.con.execute(
            "SELECT snapshot_id, snapshot_time, author, commit_message, commit_extra_info "
            f"FROM ducklake_snapshots('{ALIAS}') ORDER BY snapshot_id"
        ).pl()

    def files(self, contract: TableContract) -> list[str]:
        """Data files of a table, from the catalog (never from a storage listing)."""
        rows = self.con.execute(
            f"SELECT data_file FROM ducklake_list_files('{ALIAS}', ?, schema => ?)",
            [contract.table_name, contract.schema_name],
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        if self._con is not None:
            release(self.cfg, self._con)
            self._con = None

    def __enter__(self) -> Lake:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect(cfg: LakeConfig | None = None, *, read_only: bool = False) -> Lake:
    return Lake(cfg, read_only=read_only)
