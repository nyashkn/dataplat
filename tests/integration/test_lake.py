from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from dataplat.contracts import Column, ContractError, TableContract
from dataplat.errors import LakeError
from dataplat.lakecore import Lake
from dataplat.lakecore.write import Writer
from dataplat.trust import census, deep_census
from tests.conftest import DIM, EVENTS, events_frame

D1, D2 = date(2026, 9, 1), date(2026, 9, 2)


def test_commit_is_atomic_idempotent_and_logged(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    r1 = writer.commit_partition(EVENTS, D1, events_frame(D1, 3), run_id="usage_daily:2026-09-01")
    r2 = writer.commit_partition(EVENTS, D1, events_frame(D1, 3), run_id="usage_daily:2026-09-01:r2")
    assert r2.snapshot_id > r1.snapshot_id
    with lake.reader() as r:
        assert r.scan(EVENTS).height == 3  # replaced, not appended
        log = r.runlog()
    assert log.filter(pl.col("dataset") == "usage.events").row(0, named=True)["run_id"].endswith(":r2")
    snaps = lake.snapshots()
    assert snaps["commit_extra_info"].str.contains("usage_daily:2026-09-01:r2").any()
    assert snaps["author"].drop_nulls().to_list()[-1] == "dataplat"


def test_reader_is_pinned_to_its_snapshot(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="a")
    with lake.reader() as pinned:
        writer.commit_partition(EVENTS, D2, events_frame(D2, 5), run_id="b")
        assert pinned.scan(EVENTS).height == 2
        with pytest.raises(duckdb.Error, match="read-only"):
            pinned.sql("DELETE FROM usage.events")
        with pytest.raises(ValueError, match="schema.table"):
            pinned.sql("SELECT count(*) FROM lake.usage.events")  # would bypass the pin
    with lake.reader() as fresh:
        assert fresh.scan(EVENTS).height == 7


def test_read_only_lake_cannot_write(lake: Lake, writer: Writer, lake_config) -> None:  # type: ignore[no-untyped-def]
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="a")
    lake.close()  # a DuckDB-file catalog allows one instance per process
    with Lake(lake_config, read_only=True) as ro:
        with pytest.raises(duckdb.Error, match="read-only"):
            ro.con.execute("DELETE FROM lake.usage.events")
        with pytest.raises(LakeError, match="read-write"):
            Writer(ro)
        with ro.reader() as r:
            assert r.scan(EVENTS).height == 2


def test_failed_commit_leaves_nothing(lake: Lake, writer: Writer, monkeypatch: pytest.MonkeyPatch) -> None:
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="a")
    before = lake.current_snapshot()

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("crash after DELETE")

    real_insert = Writer._insert

    def insert_then_crash(self: Writer, con: duckdb.DuckDBPyConnection, c: TableContract, f: pl.DataFrame) -> None:
        real_insert(self, con, c, f)
        boom()

    monkeypatch.setattr(Writer, "_insert", insert_then_crash)
    with pytest.raises(RuntimeError, match="crash"):
        writer.commit_partition(EVENTS, D1, events_frame(D1, 9), run_id="b")
    assert lake.current_snapshot() == before
    with lake.reader() as r:
        assert r.scan(EVENTS).height == 2
        assert r.runlog()["run_id"].to_list() == ["a"]


def test_refusals(writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    with pytest.raises(LakeError, match="empty partition"):
        writer.commit_partition(EVENTS, D1, EVENTS.empty(), run_id="x")
    with pytest.raises(LakeError, match="requires a note"):
        writer.commit_partition(EVENTS, D1, EVENTS.empty(), run_id="x", allow_empty=True)
    receipt = writer.commit_partition(
        EVENTS, D1, EVENTS.empty(), run_id="x", allow_empty=True, note="source confirmed no traffic (outage)"
    )
    assert receipt.rows == 0
    with pytest.raises(LakeError, match="One commit = one partition"):
        writer.commit_partition(EVENTS, D2, events_frame(D1, 1), run_id="y")
    with pytest.raises(ContractError):
        writer.commit_partition(EVENTS, D1, events_frame(D1).drop("amount"), run_id="z")


def test_contract_drift_is_detected(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    drifted = TableContract(
        name=EVENTS.name,
        table=EVENTS.table,
        columns=(*EVENTS.columns[:-1], Column("amount", "float64")),
        partition_by="event_date",
    )
    with pytest.raises(ContractError, match="drifted"):
        writer.ensure_table(drifted)


def test_append_is_exactly_once_per_run_id(lake: Lake, writer: Writer) -> None:
    log = TableContract("Obs", "pulse.obs", (Column("artist", "string", required=True), Column("n", "int64")))
    writer.ensure_table(log)
    frame = pl.DataFrame({"artist": ["a", "b"], "n": [1, 2]})
    first = writer.append(log, frame, run_id="pulse:2026-09-01")
    again = writer.append(log, frame, run_id="pulse:2026-09-01")  # crash-and-retry replay
    assert again.snapshot_id == first.snapshot_id
    with lake.reader() as r:
        assert r.scan(log).height == 2


def test_replace_dimension(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(DIM)
    writer.replace(DIM, pl.DataFrame({"song_id": [1, 2], "title": ["a", "b"]}), run_id="dim:1")
    writer.replace(DIM, pl.DataFrame({"song_id": [3], "title": ["c"]}), run_id="dim:2")
    with lake.reader() as r:
        assert r.scan(DIM)["song_id"].to_list() == [3]


def test_register_frozen_files_without_rewrite(lake: Lake, writer: Writer, tmp_path: Path) -> None:
    writer.ensure_table(EVENTS)
    legacy = tmp_path / "legacy" / "event_date=2026-08-31" / "part-0.parquet"
    legacy.parent.mkdir(parents=True)
    events_frame(date(2026, 8, 31), 4).drop("event_date").write_parquet(legacy)
    receipt = writer.register_files(EVENTS, date(2026, 8, 31), [str(legacy)], run_id="migrate:0831", expected_rows=4)
    assert receipt.rows == 4
    assert lake.files(EVENTS) == [str(legacy)]  # adopted in place, not copied
    with pytest.raises(LakeError, match="already has"):
        writer.register_files(EVENTS, date(2026, 8, 31), [str(legacy)], run_id="migrate:again")


def test_repair_with_witness_and_restore(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    bad = events_frame(D1, 3, phone_prefix="+").with_columns(pl.col("phone_raw").alias("phone_number"))
    # The table's own pattern would reject '+', so seed via an unchecked contract variant.
    loose = TableContract(
        EVENTS.name,
        EVENTS.table,
        tuple(Column(c.name, c.dtype, c.required) for c in EVENTS.columns),
        partition_by="event_date",
    )
    seed = writer.commit_partition(loose, D1, bad, run_id="seed")

    def derive(e: pl.Expr) -> pl.Expr:
        return e.str.strip_prefix("+")

    rep = writer.repair_partition(
        EVENTS,
        D1,
        column="phone_number",
        witness="phone_raw",
        derive=derive,
        reason="issue #160 leading plus",
        run_id="repair:160",
    )
    assert rep.mode == "repair" and rep.stats["changed"] == 3 and rep.stats["pre_snapshot"] == seed.snapshot_id
    with lake.reader() as r:
        fixed = r.scan(EVENTS)
    assert not fixed["phone_number"].str.starts_with("+").any()
    assert fixed["phone_raw"].str.starts_with("+").all()  # witness untouched
    noop = writer.repair_partition(
        EVENTS, D1, column="phone_number", witness="phone_raw", derive=derive, reason="rerun", run_id="repair:160b"
    )
    assert noop.mode == "repair-noop"
    back = writer.restore_partition(loose, D1, snapshot=seed.snapshot_id, run_id="undo", reason="demo")
    assert back.mode == "restore"
    with lake.reader() as r:
        assert r.scan(loose)["phone_number"].str.starts_with("+").all()


def test_census_reports_gaps_zero_rows_and_lag(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 3), run_id="a")
    writer.commit_partition(EVENTS, date(2026, 9, 3), EVENTS.empty(), run_id="b", allow_empty=True, note="outage")
    with lake.reader() as r:
        c = census(r, [EVENTS], today=date(2026, 9, 5)).row(0, named=True)
        assert deep_census(r, EVENTS).height == 0
    assert c["partitions"] == 2 and c["rows"] == 3 and c["zero_row_partitions"] == 1
    assert c["missing_partitions"] == 2  # 09-02 and 09-04 (09-05 is today)
    assert c["lag_days"] == 2


def test_conflicting_concurrent_overwrite_is_rejected_not_merged(lake: Lake, writer: Writer, lake_config) -> None:  # type: ignore[no-untyped-def]
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="seed")
    other = Lake(lake_config)
    other.con.execute("BEGIN")
    other.con.execute("DELETE FROM lake.usage.events WHERE event_date = ?", [D1])
    writer.commit_partition(EVENTS, D1, events_frame(D1, 5), run_id="winner")
    with pytest.raises(duckdb.TransactionException, match="onflict"):
        other.con.execute("COMMIT")
    other.close()
    with lake.reader() as r:
        assert r.scan(EVENTS).height == 5


def test_writer_retries_a_conflict(writer: Writer, lake: Lake, monkeypatch: pytest.MonkeyPatch) -> None:
    writer.ensure_table(EVENTS)
    calls = {"n": 0}
    real = Writer._insert

    def flaky(self: Writer, con: duckdb.DuckDBPyConnection, c: TableContract, f: pl.DataFrame) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise duckdb.TransactionException("Transaction conflict - simulated")
        real(self, con, c, f)

    monkeypatch.setattr(Writer, "_insert", flaky)
    monkeypatch.setattr("dataplat.lakecore.write.CONFLICT_BACKOFF_S", 0)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="retry")
    assert calls["n"] == 2
    with lake.reader() as r:
        assert r.scan(EVENTS).height == 2 and r.runlog().height == 1


def test_one_instance_per_catalog_per_process(lake: Lake, writer: Writer, lake_config) -> None:  # type: ignore[no-untyped-def]
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 2), run_id="a")
    with Lake(lake_config, read_only=True) as ro:  # shares the writer's instance
        with pytest.raises(LakeError, match="read-write"):
            Writer(ro)
        with ro.reader() as r:
            assert r.scan(EVENTS).height == 2
    lake.close()
    with Lake(lake_config, read_only=True) as ro_first:  # no writer in the process: a read-only attach
        assert ro_first.current_snapshot() > 0
        with pytest.raises(LakeError, match="already opened the lake read-only"):
            Lake(lake_config).con  # noqa: B018 - opening is the assertion


def test_reading_an_unwritten_namespace_says_so(lake_config) -> None:  # type: ignore[no-untyped-def]
    fresh = lake_config.for_namespace("never_written")
    with pytest.raises(LakeError, match="no lake in namespace 'never_written' yet"):
        Lake(fresh, read_only=True).con  # noqa: B018 - opening is the assertion
    with Lake(fresh) as lk:  # a writer may create it
        Writer(lk)
    with Lake(fresh, read_only=True) as ro:
        assert ro.current_snapshot() >= 0


def test_review_fixes_writer_edges(lake: Lake, writer: Writer, tmp_path: Path) -> None:
    """Edges found in review: containment on register, empty registers, witness==column, append on partitioned."""
    writer.ensure_table(EVENTS)
    stray = tmp_path / "legacy" / "event_date=2026-08-30" / "part-0.parquet"
    stray.parent.mkdir(parents=True)
    events_frame(date(2026, 8, 30), 3).drop("event_date").write_parquet(stray)
    with pytest.raises(LakeError, match="different partition"):
        writer.register_files(EVENTS, date(2026, 8, 31), [str(stray)], run_id="wrong-partition")
    with lake.reader() as r:
        assert r.scan(EVENTS).height == 0  # rolled back: nothing adopted anywhere
    empty = tmp_path / "legacy" / "event_date=2026-08-29" / "part-0.parquet"
    empty.parent.mkdir(parents=True)
    events_frame(date(2026, 8, 29), 1).drop("event_date").head(0).write_parquet(empty)
    with pytest.raises(LakeError, match="no rows"):
        writer.register_files(EVENTS, date(2026, 8, 29), [str(empty)], run_id="empty")
    with pytest.raises(LakeError, match="witness must be a different"):
        writer.repair_partition(
            EVENTS, D1, column="phone_raw", witness="phone_raw", derive=lambda e: e, reason="x", run_id="r"
        )
    with pytest.raises(LakeError, match="partitioned"):
        writer.append(EVENTS, events_frame(D1, 1), run_id="a")


def test_append_only_census_sums_appends(lake: Lake, writer: Writer) -> None:
    log = TableContract("Obs", "pulse.obs", (Column("artist", "string", required=True),))
    writer.ensure_table(log)
    writer.append(log, pl.DataFrame({"artist": ["a", "b", "c"]}), run_id="p1")
    writer.append(log, pl.DataFrame({"artist": ["d"]}), run_id="p2")
    with lake.reader() as r:
        assert census(r, [log]).row(0, named=True)["rows"] == 4


def test_partitioning_drift_is_detected(lake: Lake, writer: Writer) -> None:
    lake.con.execute("CREATE SCHEMA IF NOT EXISTS lake.usage")
    lake.con.execute(f"CREATE TABLE lake.usage.events ({EVENTS.ddl_columns()})")  # created without partitioning
    with pytest.raises(ContractError, match="partitioned by"):
        writer.ensure_table(EVENTS)


def test_reader_pin_guard_is_case_and_quote_insensitive(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    with lake.reader() as r:
        for q in (
            "SELECT * FROM LAKE.usage.events",
            'SELECT * FROM "lake"."usage"."events"',
            "select 1; delete from lake.usage.events",
        ):
            with pytest.raises(ValueError, match="schema.table"):
                r.sql(q)
