"""Alerts as code: payloads, and the SQL run against a synthetic traces stream (DuckDB; DataFusion too
when installed, since OpenObserve runs DataFusion)."""

from __future__ import annotations

import time
from typing import Any

import duckdb
import pytest

from dataplat.errors import ConfigError
from dataplat.observe.alerts import Alert, cron_interval_minutes, standard_alerts, workflow_name


def usage_daily_scheduled() -> None: ...


usage_daily_scheduled.dbos_function_name = "usage_daily_scheduled"  # type: ignore[attr-defined]
SCHEDULES = [{"schedule_name": "usage_daily", "workflow_fn": usage_daily_scheduled, "schedule": "20 3 * * *"}]
NOW_US = int(time.time() * 1_000_000)
MIN_US = 60 * 1_000_000


def test_standard_alerts_cover_failures_new_errors_and_each_schedule() -> None:
    alerts = {a.name: a for a in standard_alerts("Mdundo-Platform", SCHEDULES)}
    assert sorted(alerts) == [
        "mdundo_platform_heartbeat_usage_daily",
        "mdundo_platform_new_errors",
        "mdundo_platform_workflow_failures",
    ]
    beat = alerts["mdundo_platform_heartbeat_usage_daily"]
    assert beat.period_minutes == 24 * 60 + 120  # daily cron + two hours' grace
    payload = beat.payload(["ops"])
    assert payload["stream_type"] == "traces" and payload["stream_name"] == "mdundo_platform"
    assert payload["query_condition"] == {"type": "sql", "sql": beat.sql}
    assert payload["trigger_condition"]["operator"] == ">=" and payload["trigger_condition"]["threshold"] == 1
    assert payload["destinations"] == ["ops"] and payload["enabled"] is True


def test_cron_intervals_and_names() -> None:
    assert cron_interval_minutes("*/15 * * * *") == 15
    assert cron_interval_minutes("0 6 * * 1") == 7 * 24 * 60
    assert workflow_name(usage_daily_scheduled) == "usage_daily_scheduled"
    with pytest.raises(ConfigError, match="snake_case"):
        Alert("Bad Name", "", "SELECT 1", 1, 1, 1, "s")
    with pytest.raises(ConfigError, match="cannot be used"):
        standard_alerts("x'; DROP TABLE y; --")


def _spans() -> list[tuple[Any, ...]]:
    # service, operation, status, operation type, error signature, timestamp (µs)
    return [
        ("mdundo", "usage_daily", "ERROR", "workflow", "payout failed for 9{12}", NOW_US - 5 * MIN_US),
        ("mdundo", "usage_daily", "ERROR", "workflow", "payout failed for 9{12}", NOW_US - 6 * MIN_US),
        ("mdundo", "usage_daily", "ERROR", "workflow", "stale partition", NOW_US - 3 * 24 * 60 * MIN_US),
        ("mdundo", "usage_daily", "ERROR", "workflow", "stale partition", NOW_US - 4 * MIN_US),
        ("mdundo", "build_and_commit", "ERROR", "step", "retried then fine", NOW_US - 4 * MIN_US),
        ("mdundo-feat_x", "usage_daily", "ERROR", "workflow", "worktree noise", NOW_US - 4 * MIN_US),
        ("mdundo", "graph_publish_scheduled", "OK", "workflow", "", NOW_US - 60 * MIN_US),
    ]


def _duckdb(sql: str, rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    con = duckdb.connect()
    con.execute(
        'CREATE TABLE "mdundo" (service_name VARCHAR, operation_name VARCHAR, span_status VARCHAR, '
        "dbos_operation_type VARCHAR, dataplat_error VARCHAR, _timestamp BIGINT)"
    )
    if rows:
        con.executemany('INSERT INTO "mdundo" VALUES (?, ?, ?, ?, ?, ?)', rows)
    return con.execute(sql).fetchall()


def _datafusion(sql: str, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    datafusion = pytest.importorskip("datafusion")
    import pyarrow as pa

    cols = ["service_name", "operation_name", "span_status", "dbos_operation_type", "dataplat_error", "_timestamp"]
    table = pa.table(
        {c: [r[i] for r in rows] for i, c in enumerate(cols)},
        schema=pa.schema([(c, pa.int64() if c == "_timestamp" else pa.string()) for c in cols]),
    )
    ctx = datafusion.SessionContext()
    ctx.register_record_batches("mdundo", [table.to_batches() or [pa.RecordBatch.from_pylist([], schema=table.schema)]])
    return ctx.sql(sql).to_arrow_table().to_pylist()


ENGINES = {"duckdb": _duckdb, "datafusion": _datafusion}


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_alert_queries_return_one_row_per_thing_to_act_on(engine: str) -> None:
    run = ENGINES[engine]
    alerts = {a.name.removeprefix("mdundo_"): a for a in standard_alerts("mdundo", SCHEDULES)}
    failures = run(alerts["workflow_failures"].sql, _spans())
    assert len(failures) == 2  # two signatures on main's workflows; steps and worktrees excluded
    new = run(alerts["new_errors"].sql, _spans())
    assert len(new) == 1  # "stale partition" was already seen three days ago
    assert (
        run(alerts["heartbeat_usage_daily"].sql, _spans())
        and len(run(alerts["heartbeat_usage_daily"].sql, _spans())) == 1
    )
    ok_run = ("mdundo", "usage_daily_scheduled", "OK", "workflow", "", NOW_US - 30 * MIN_US)
    assert len(run(alerts["heartbeat_usage_daily"].sql, [*_spans(), ok_run])) == 0
