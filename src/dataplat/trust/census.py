"""Census: what the lake actually holds, read from the run log at one pinned snapshot.

``just census`` answers the questions that hurt last time, in counts only:

- which datasets exist, how many partitions and rows each has;
- which daily partitions are missing between ``expected_start`` and yesterday (gaps);
- which partitions committed zero rows (e.g. ``fact_charges_topline``'s 20 empty days);
- how stale each dataset is (lag in days);
- how many code versions wrote it (a mix usually means some partitions predate a fix).

``deep_census`` also counts rows per partition in the table and compares them with the run log. A
mismatch means something wrote around the writer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import polars as pl

from dataplat.contracts.model import TableContract
from dataplat.lakecore.read import Reader

_SCHEMA = pl.Schema(
    {
        "dataset": pl.String(),
        "partitions": pl.Int64(),
        "rows": pl.Int64(),
        "zero_row_partitions": pl.Int64(),
        "missing_partitions": pl.Int64(),
        "first_partition": pl.String(),
        "last_partition": pl.String(),
        "lag_days": pl.Int64(),
        "code_versions": pl.Int64(),
        "last_commit": pl.Datetime("us", "UTC"),
    }
)


def today_utc() -> date:
    return datetime.now(UTC).date()


def days_between(start: date, end: date) -> list[str]:
    """ISO dates from ``start`` to ``end`` inclusive."""
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def expected_days(contract: TableContract, today: date) -> list[str]:
    """Daily partitions expected from ``expected_start`` through yesterday (UTC)."""
    if contract.cadence != "daily" or not contract.expected_start:
        return []
    return days_between(date.fromisoformat(contract.expected_start), today - timedelta(days=1))


def census(reader: Reader, contracts: Sequence[TableContract] = (), *, today: date | None = None) -> pl.DataFrame:
    today = today or today_utc()
    log = reader.runlog()
    by_name = {c.table: c for c in contracts}
    rows: list[dict[str, object]] = []
    datasets = sorted(set(log["dataset"].to_list()) | set(by_name))
    for ds in datasets:
        part = log.filter(pl.col("dataset") == ds)
        present = set(part["partition"].drop_nulls().to_list())
        c = by_name.get(ds)
        expected = expected_days(c, today) if c else []
        last = max(present) if present else None
        lag = None
        if c is not None and c.cadence == "daily" and last:
            lag = (today - date.fromisoformat(last)).days
        rows.append(
            {
                "dataset": ds,
                "partitions": part.height,
                "rows": int(part["rows"].sum()) if part.height else 0,
                "zero_row_partitions": int((part["rows"] == 0).sum()) if part.height else 0,
                "missing_partitions": len(set(expected) - present) if expected else None,
                "first_partition": min(present) if present else None,
                "last_partition": last,
                "lag_days": lag,
                "code_versions": part["code_version"].n_unique() if part.height else 0,
                "last_commit": part["committed_at"].max() if part.height else None,
            }
        )
    return pl.DataFrame(rows, schema=_SCHEMA)


def missing_partitions(reader: Reader, contract: TableContract, *, today: date | None = None) -> list[str]:
    log = reader.runlog().filter(pl.col("dataset") == contract.table)
    return sorted(set(expected_days(contract, today or today_utc())) - set(log["partition"].to_list()))


def deep_census(reader: Reader, contract: TableContract) -> pl.DataFrame:
    """Per-partition row counts: run log vs. table. Returns only mismatching partitions."""
    pcol = contract.partition_by
    if pcol is None or not reader.has_table(contract):
        return pl.DataFrame(schema={"partition": pl.String, "runlog_rows": pl.Int64, "table_rows": pl.Int64})
    actual = reader.sql(
        f'SELECT CAST("{pcol}" AS VARCHAR) AS "partition", count(*) AS table_rows FROM {contract.table} GROUP BY ALL'
    )
    log = (
        reader.runlog()
        .filter(pl.col("dataset") == contract.table)
        .select(pl.col("partition"), pl.col("rows").alias("runlog_rows"))
    )
    joined = log.join(actual, on="partition", how="full", coalesce=True).with_columns(
        pl.col("runlog_rows").fill_null(0), pl.col("table_rows").fill_null(0)
    )
    return joined.filter(pl.col("runlog_rows") != pl.col("table_rows")).sort("partition")
