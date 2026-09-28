"""Write side of the lake: the only code path that changes lake tables.

Projects may import this module only from ``<pkg>.pipelines``. ``dataplat lint`` enforces that (rule
DPA004).

Every write is one DuckLake transaction holding three things: the data change, a row in
``_meta.dataset_runs``, and a commit message carrying the run id. It follows that:

- a partition exists exactly when its run-log row says so, because the two commit together or not at all;
- re-running a partition replaces it (DELETE + INSERT in one transaction), so pipelines are idempotent;
- when two writers race on one partition, one commits and the other gets a conflict and retries;
- every snapshot records which run produced it (``Lake.snapshots()``);
- a zero-row partition is refused unless the caller says why (see "20 zero-row days" in docs/traps.md).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import polars as pl

from dataplat.checks.verdict import Verdict
from dataplat.checks.witness import verify_repair
from dataplat.contracts.model import TableContract
from dataplat.errors import ContractError, LakeError, PartitionExistsError
from dataplat.lakecore._connect import ALIAS, META_CATALOG, _q
from dataplat.lakecore.read import RUNLOG, Lake

PartitionValue = date | str | int

RUNLOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {ALIAS}.{RUNLOG} (
    dataset VARCHAR NOT NULL,
    "partition" VARCHAR,
    mode VARCHAR NOT NULL,
    "rows" BIGINT NOT NULL,
    run_id VARCHAR NOT NULL,
    code_version VARCHAR NOT NULL,
    contract_hash VARCHAR NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    stats VARCHAR,
    note VARCHAR
)
"""

_DESCRIBE_ALIASES = {"TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ"}
MAX_CONFLICT_RETRIES = 3
CONFLICT_BACKOFF_S = 0.2


@dataclass(frozen=True)
class Receipt:
    dataset: str
    partition: str | None
    mode: str
    rows: int
    run_id: str
    code_version: str
    contract_hash: str
    snapshot_id: int
    stats: dict[str, Any] = field(default_factory=dict)


class Writer:
    def __init__(self, lake: Lake) -> None:
        if lake.read_only:
            raise LakeError("Writer needs a read-write Lake; this one was opened read_only=True")
        self.lake = lake
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {ALIAS}._meta")
        self.con.execute(RUNLOG_DDL)

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        return self.lake.con

    # ---------------------------------------------------------------- schema
    def ensure_table(self, contract: TableContract) -> None:
        """Create the table from its contract, or verify the existing table still matches it.

        Creation and partitioning commit together (a crash can't leave an unpartitioned table). Drift in
        columns, types or partitioning is an error, never auto-migrated: write a reviewed migration
        pipeline that alters the table, then update the contract.
        """
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {ALIAS}.{contract.schema_name}")
        row = self.con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            [ALIAS, contract.schema_name, contract.table_name],
        ).fetchone()
        if row and row[0]:
            actual = [
                (r[0], _DESCRIBE_ALIASES.get(r[1], r[1]))
                for r in self.con.execute(f"DESCRIBE {contract.qualified(ALIAS)}").fetchall()
            ]
            want = [(c.name, c.duckdb) for c in contract.columns]
            parts = self._partition_columns(contract)
            want_parts = [contract.partition_by] if contract.partition_by else []
            if actual != want or parts != want_parts:
                raise ContractError(
                    f"{contract.table} drifted from its contract.\n"
                    f"  lake:     {actual} partitioned by {parts}\n"
                    f"  contract: {want} partitioned by {want_parts}\n"
                    "Write a reviewed migration; the writer never alters tables implicitly."
                )
            return
        self.con.execute("BEGIN")
        try:
            self.con.execute(f"CREATE TABLE {contract.qualified(ALIAS)} ({contract.ddl_columns()})")
            if contract.partition_by:
                self.con.execute(
                    f'ALTER TABLE {contract.qualified(ALIAS)} SET PARTITIONED BY ("{contract.partition_by}")'
                )
            self.con.execute("COMMIT")
        except BaseException:
            _rollback(self.con)
            raise

    def _partition_columns(self, contract: TableContract) -> list[str]:
        """Current partition columns of a table, from DuckLake's metadata tables."""
        meta = f"{META_CATALOG}.{self.lake.cfg.metadata_schema() or 'main'}"
        rows = self.con.execute(
            f"""
            SELECT c.column_name
            FROM {meta}.ducklake_partition_info pi
            JOIN {meta}.ducklake_partition_column pc
              ON pc.partition_id = pi.partition_id AND pc.table_id = pi.table_id
            JOIN {meta}.ducklake_column c
              ON c.column_id = pc.column_id AND c.table_id = pc.table_id AND c.end_snapshot IS NULL
            JOIN {meta}.ducklake_table t ON t.table_id = pi.table_id AND t.end_snapshot IS NULL
            JOIN {meta}.ducklake_schema sc ON sc.schema_id = t.schema_id AND sc.end_snapshot IS NULL
            WHERE pi.end_snapshot IS NULL AND sc.schema_name = ? AND t.table_name = ?
            ORDER BY pc.partition_key_index
            """,
            [contract.schema_name, contract.table_name],
        ).fetchall()
        return [r[0] for r in rows]

    # ---------------------------------------------------------------- writes
    def commit_partition(
        self,
        contract: TableContract,
        partition: PartitionValue,
        frame: pl.DataFrame,
        *,
        run_id: str,
        stats: dict[str, Any] | None = None,
        note: str | None = None,
        allow_empty: bool = False,
        _mode: str = "overwrite_partition",
    ) -> Receipt:
        """Replace one partition atomically. Re-running with the same input gives the same partition."""
        pcol = contract.partition_by
        if pcol is None:
            raise LakeError(f"{contract.table} is not partitioned; use replace() or append()")
        contract.validate(frame)
        if frame.height:
            values = frame[pcol].unique()
            if values.len() != 1 or values[0] != partition:
                raise LakeError(
                    f"{contract.table}: frame holds {values.len()} distinct {pcol} value(s), "
                    f"commit is for {partition!r}. One commit = one partition."
                )
        elif not allow_empty:
            raise LakeError(
                f"refusing to commit an empty partition {contract.table}[{partition}]. An empty "
                "partition usually means the source failed, not that nothing happened. Pass "
                "allow_empty=True with note='why the source had no rows' if it is real."
            )
        if allow_empty and frame.height == 0 and not note:
            raise LakeError("allow_empty=True requires a note explaining why the partition is empty")

        def body(con: duckdb.DuckDBPyConnection) -> None:
            con.execute(f'DELETE FROM {contract.qualified(ALIAS)} WHERE "{pcol}" = ?', [partition])
            self._insert(con, contract, frame)

        return self._commit(contract, _mode, str(partition), frame.height, body, run_id, stats, note)

    def replace(
        self,
        contract: TableContract,
        frame: pl.DataFrame,
        *,
        run_id: str,
        stats: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> Receipt:
        """Full refresh of an unpartitioned table (dimensions) in one transaction."""
        if contract.partition_by is not None:
            raise LakeError(f"{contract.table} is partitioned; use commit_partition()")
        contract.validate(frame)
        if frame.height == 0:
            raise LakeError(f"refusing to replace {contract.table} with zero rows")

        def body(con: duckdb.DuckDBPyConnection) -> None:
            con.execute(f"DELETE FROM {contract.qualified(ALIAS)}")
            self._insert(con, contract, frame)

        return self._commit(contract, "replace", None, frame.height, body, run_id, stats, note)

    def append(
        self,
        contract: TableContract,
        frame: pl.DataFrame,
        *,
        run_id: str,
        stats: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> Receipt:
        """Append rows exactly once per ``run_id``.

        A crash between COMMIT and the orchestrator recording success makes it run the step again.
        The run log is checked inside the transaction, so a repeated run_id appends nothing.
        """
        if contract.partition_by is not None:
            raise LakeError(f"{contract.table} is partitioned; use commit_partition() (one run-log row per partition)")
        contract.validate(frame)
        already: list[Receipt] = []

        def body(con: duckdb.DuckDBPyConnection) -> None:
            n = con.execute(
                f"SELECT count(*) FROM {ALIAS}.{RUNLOG} WHERE dataset = ? AND run_id = ? AND mode = 'append'",
                [contract.table, run_id],
            ).fetchone()[0]  # type: ignore[index]
            if n:
                already.append(self._receipt_for(contract, run_id))
                raise _AlreadyCommitted
            self._insert(con, contract, frame)

        try:
            return self._commit(contract, "append", None, frame.height, body, run_id, stats, note)
        except _AlreadyCommitted:
            return already[0]

    def register_files(
        self,
        contract: TableContract,
        partition: PartitionValue,
        files: Sequence[str],
        *,
        run_id: str,
        expected_rows: int | None = None,
        note: str | None = None,
        allow_empty: bool = False,
    ) -> Receipt:
        """Adopt existing hive-partitioned Parquet files into the catalog without rewriting them.

        Used to migrate frozen history. ``files`` comes from a manifest, never from a storage listing.
        Every adopted row must land in ``partition`` (a file whose hive path names another partition is
        refused), and an empty result is refused unless ``allow_empty`` with a note.
        """
        if allow_empty and not note:
            raise LakeError("allow_empty=True requires a note explaining why the partition is empty")
        pcol = contract.partition_by
        if pcol is None:
            raise LakeError("register_files needs a partitioned table")
        if not files:
            raise LakeError("no files given")
        rows_box: list[int] = []

        def count(con: duckdb.DuckDBPyConnection, where: str = "", params: list[Any] | None = None) -> int:
            row = con.execute(f"SELECT count(*) FROM {contract.qualified(ALIAS)} {where}", params or []).fetchone()
            return int(row[0]) if row else 0

        def body(con: duckdb.DuckDBPyConnection) -> None:
            total_before = count(con)
            present = count(con, f'WHERE "{pcol}" = ?', [partition])
            if present:
                raise PartitionExistsError(
                    f"{contract.table}[{partition}] already has {present} rows; not registering over it"
                )
            for f in files:
                con.execute(
                    f"CALL ducklake_add_data_files('{ALIAS}', {_q(contract.table_name)}, {_q(f)}, "
                    f"schema => {_q(contract.schema_name)})"
                )
            got = count(con, f'WHERE "{pcol}" = ?', [partition])
            added = count(con) - total_before
            if added != got:
                raise LakeError(
                    f"files added {added} rows but only {got} belong to {pcol}={partition}; "
                    "a file's hive path names a different partition"
                )
            if got == 0 and not allow_empty:
                raise LakeError(f"registering {len(files)} file(s) added no rows to {partition}")
            if expected_rows is not None and got != expected_rows:
                raise LakeError(f"registered {got} rows for {partition}, manifest says {expected_rows}")
            rows_box.append(got)

        receipt = self._commit(
            contract,
            "register",
            str(partition),
            -1,
            body,
            run_id,
            {"files": len(files)},
            note,
            rows_from=rows_box,
        )
        return receipt

    def repair_partition(
        self,
        contract: TableContract,
        partition: PartitionValue,
        *,
        column: str,
        witness: str,
        derive: Callable[[pl.Expr], pl.Expr],
        reason: str,
        run_id: str,
    ) -> Receipt:
        """Recompute ``column`` from its untouched ``witness`` for one partition, verified against the pre-image.

        The pre-image snapshot id is recorded in the run log. ``restore_partition`` undoes the repair.
        """
        pcol = contract.partition_by
        if pcol is None:
            raise LakeError("repair_partition needs a partitioned table")
        names = contract.column_names()
        if column == witness:
            raise LakeError(
                "the witness must be a different, untouched column (repairing the witness itself is not reversible)"
            )
        if witness not in names or column not in names:
            raise LakeError(f"{contract.table} has no {column!r}/{witness!r}; repairs need a witness column")
        pre_snapshot = self.lake.current_snapshot()
        with self.lake.reader(pre_snapshot) as r:
            pre = r.scan(contract, where=f'"{pcol}" = ?', params=[partition])
        post = pre.with_columns(derive(pl.col(witness)).alias(column))
        ev = verify_repair(pre, post, repaired=column, witness=witness, derive=derive)
        if ev.verdict is not Verdict.PASS:
            raise LakeError(f"repair refused: {ev}")
        stats = {"column": column, "witness": witness, "changed": ev.measured["changed"], "pre_snapshot": pre_snapshot}
        if ev.measured["changed"] == 0:
            return Receipt(
                contract.table,
                str(partition),
                "repair-noop",
                pre.height,
                run_id,
                self.lake.cfg.code_version,
                contract.fingerprint(),
                pre_snapshot,
                stats,
            )
        return self.commit_partition(
            contract, partition, post, run_id=run_id, stats=stats, note=f"repair: {reason}", _mode="repair"
        )

    def restore_partition(
        self, contract: TableContract, partition: PartitionValue, *, snapshot: int, run_id: str, reason: str
    ) -> Receipt:
        """Put a partition back exactly as it was at ``snapshot`` (time travel), as a new commit."""
        pcol = contract.partition_by
        if pcol is None:
            raise LakeError("restore_partition needs a partitioned table")
        with self.lake.reader(snapshot) as r:
            old = r.scan(contract, where=f'"{pcol}" = ?', params=[partition])
        if old.height == 0:
            raise LakeError(f"{contract.table}[{partition}] did not exist at snapshot {snapshot}")
        return self.commit_partition(
            contract,
            partition,
            old,
            run_id=run_id,
            stats={"restored_from": snapshot},
            note=f"restore: {reason}",
            _mode="restore",
        )

    # ---------------------------------------------------------------- internals
    def _insert(self, con: duckdb.DuckDBPyConnection, contract: TableContract, frame: pl.DataFrame) -> None:
        if frame.height == 0:
            return
        cols = ", ".join(f'"{c}"' for c in contract.column_names())
        con.register("__dataplat_frame", frame.to_arrow())
        try:
            con.execute(f"INSERT INTO {contract.qualified(ALIAS)} ({cols}) SELECT {cols} FROM __dataplat_frame")
        finally:
            con.unregister("__dataplat_frame")

    def _receipt_for(self, contract: TableContract, run_id: str) -> Receipt:
        row = self.con.execute(
            f'SELECT "partition", mode, "rows", code_version, contract_hash, stats FROM {ALIAS}.{RUNLOG} '
            "WHERE dataset = ? AND run_id = ? ORDER BY committed_at DESC LIMIT 1",
            [contract.table, run_id],
        ).fetchone()
        assert row is not None
        snap = self.con.execute(
            f"SELECT max(snapshot_id) FROM ducklake_snapshots('{ALIAS}') "
            "WHERE json_extract_string(commit_extra_info, '$.run_id') = ?",
            [run_id],
        ).fetchone()[0]  # type: ignore[index]
        return Receipt(
            contract.table,
            row[0],
            row[1],
            row[2],
            run_id,
            row[3],
            row[4],
            int(snap or -1),
            json.loads(row[5]) if row[5] else {},
        )

    def _commit(
        self,
        contract: TableContract,
        mode: str,
        partition: str | None,
        rows: int,
        body: Callable[[duckdb.DuckDBPyConnection], None],
        run_id: str,
        stats: dict[str, Any] | None,
        note: str | None,
        rows_from: list[int] | None = None,
    ) -> Receipt:
        con = self.con
        code_version = self.lake.cfg.code_version
        extra = json.dumps(
            {"run_id": run_id, "dataset": contract.table, "partition": partition, "mode": mode}, sort_keys=True
        )
        for attempt in range(MAX_CONFLICT_RETRIES + 1):
            con.execute("BEGIN")
            try:
                body(con)
                n = rows_from[-1] if rows_from else rows
                con.execute(
                    f"INSERT INTO {ALIAS}.{RUNLOG} VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp, ?, ?)",
                    [
                        contract.table,
                        partition,
                        mode,
                        n,
                        run_id,
                        code_version,
                        contract.fingerprint(),
                        json.dumps(stats, sort_keys=True, default=str) if stats else None,
                        note,
                    ],
                )
                con.execute(
                    f"CALL {ALIAS}.set_commit_message('dataplat', "
                    f"{_q(f'{mode} {contract.table}[{partition}]')}, extra_info => {_q(extra)})"
                )
                con.execute("COMMIT")
                break
            except duckdb.TransactionException as err:
                _rollback(con)
                if "conflict" not in str(err).lower() or attempt == MAX_CONFLICT_RETRIES:
                    raise LakeError(f"commit to {contract.table}[{partition}] failed: {err}") from err
                time.sleep(CONFLICT_BACKOFF_S * (attempt + 1))
            except BaseException:
                _rollback(con)
                raise
        snap = con.execute(f"SELECT id FROM {ALIAS}.last_committed_snapshot()").fetchone()[0]  # type: ignore[index]
        return Receipt(
            dataset=contract.table,
            partition=partition,
            mode=mode,
            rows=rows_from[-1] if rows_from else rows,
            run_id=run_id,
            code_version=code_version,
            contract_hash=contract.fingerprint(),
            snapshot_id=int(snap),
            stats=dict(stats or {}),
        )


class _AlreadyCommitted(Exception):
    pass


def _rollback(con: duckdb.DuckDBPyConnection) -> None:
    try:
        con.execute("ROLLBACK")
    except duckdb.Error:
        pass  # the failed COMMIT already rolled back
