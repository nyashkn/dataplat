from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from dataplat.contracts import Column, ContractError, TableContract
from dataplat.contracts.generate import render_tables
from dataplat.contracts.linkml import load_tables
from tests.conftest import EVENTS, events_frame


def test_exact_schema_no_coercion() -> None:
    good = events_frame(date(2026, 9, 1))
    EVENTS.validate(good)
    with pytest.raises(ContractError, match="dtype"):
        EVENTS.validate(good.with_columns(pl.col("user_id").cast(pl.Categorical)))
    with pytest.raises(ContractError, match="missing columns"):
        EVENTS.validate(good.drop("amount"))
    with pytest.raises(ContractError, match="column order"):
        EVENTS.validate(good.select(list(reversed(good.columns))))


def test_value_checks_report_shapes_not_values() -> None:
    bad = events_frame(date(2026, 9, 1)).with_columns(pl.lit("tz").alias("country_iso"))
    with pytest.raises(ContractError) as err:
        EVENTS.validate(bad)
    assert "country_iso" in str(err.value) and "a{2}" in str(err.value)
    nulls = events_frame(date(2026, 9, 1)).with_columns(pl.lit(None, pl.String).alias("user_id"))
    with pytest.raises(ContractError):
        EVENTS.validate(nulls)


def test_grain_uniqueness(dim_contract: TableContract) -> None:
    dup = pl.DataFrame({"song_id": [1, 1], "title": ["a", "b"]})
    with pytest.raises(ContractError):
        dim_contract.validate(dup)


def test_contract_definition_errors() -> None:
    with pytest.raises(ContractError, match="not a column"):
        TableContract("X", "s.x", (Column("a", "string"),), partition_by="b")
    with pytest.raises(ContractError, match="unknown dtype"):
        TableContract("X", "s.x", (Column("a", "money"),))
    with pytest.raises(ContractError, match="schema"):
        TableContract("X", "x", (Column("a", "string"),))


def test_fingerprint_is_stable_and_ignores_checks() -> None:
    assert EVENTS.fingerprint() == EVENTS.fingerprint()
    from dataplat.checks import check

    @check(name="tmp_always_true")
    def always() -> pl.Expr:
        return pl.lit(True)

    assert EVENTS.with_checks(always).fingerprint() == EVENTS.fingerprint()


SCHEMA = textwrap.dedent(
    """
    id: https://example.org/tables
    name: tables
    prefixes: {linkml: https://w3id.org/linkml/}
    imports: [linkml:types]
    default_range: string
    enums:
      Channel:
        permissible_values: {sms: {}, web: {}}
    slots:
      event_date: {range: date, required: true}
      user_id: {range: string, required: true}
      country_iso: {range: string, required: true, pattern: "^[A-Z]{2,3}$"}
      channel: {range: Channel}
      amount: {range: decimal, annotations: {dataplat.dtype: "decimal(18,4)"}}
      plays: {range: integer, minimum_value: 0}
    classes:
      EventsClean:
        description: One row per event.
        annotations:
          dataplat.table: usage.events_clean
          dataplat.partition_by: event_date
          dataplat.grain: none
          dataplat.cadence: daily
          dataplat.expected_start: "2026-09-01"
        slots: [event_date, user_id, country_iso, channel, amount, plays]
      NotATable:
        slots: [user_id]
    """
)


def test_linkml_to_contract_and_deterministic_render(tmp_path: Path) -> None:
    p = tmp_path / "tables.yaml"
    p.write_text(SCHEMA)
    [t] = load_tables(p)
    assert t.table == "usage.events_clean" and t.partition_by == "event_date" and t.grain == ()
    cols = {c.name: c for c in t.columns}
    assert cols["event_date"].dtype == "date" and cols["event_date"].required
    assert cols["channel"].allowed == ("sms", "web")
    assert cols["amount"].dtype == "decimal(18,4)"
    assert cols["plays"].minimum == 0
    src = render_tables([t], "tables.yaml")
    assert src == render_tables(load_tables(p), "tables.yaml")
    ns: dict[str, object] = {}
    exec(compile(src, "generated", "exec"), ns)
    assert ns["EVENTS_CLEAN"] == t


def test_linkml_refuses_forbidden_columns_and_implicit_decimals(tmp_path: Path) -> None:
    p = tmp_path / "tables.yaml"
    p.write_text(SCHEMA)
    with pytest.raises(ContractError, match="forbidden column"):
        load_tables(p, forbidden_columns=["user_id"])
    p.write_text(SCHEMA.replace(', annotations: {dataplat.dtype: "decimal(18,4)"}', ""))
    with pytest.raises(ContractError, match="decimal needs"):
        load_tables(p)
