from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from dataplat.contracts import Column, TableContract

pytest_plugins = ["pytester"]

EVENTS = TableContract(
    name="Events",
    table="usage.events",
    columns=(
        Column("event_date", "date", required=True),
        Column("user_id", "string", required=True),
        Column("phone_raw", "string"),
        Column("phone_number", "string", pattern=r"^[0-9A-Za-z]+$"),
        Column("country_iso", "string", required=True, pattern=r"^[A-Z]{2,3}$"),
        Column("amount", "decimal(18,4)"),
    ),
    partition_by="event_date",
    cadence="daily",
    expected_start="2026-09-01",
)

DIM = TableContract(
    name="Songs",
    table="catalog.songs",
    columns=(Column("song_id", "int64", required=True), Column("title", "string")),
    grain=("song_id",),
)


def events_frame(day: date, n: int = 3, *, phone_prefix: str = "") -> pl.DataFrame:
    from decimal import Decimal

    return pl.DataFrame(
        {
            "event_date": [day] * n,
            "user_id": [f"u{i}" for i in range(n)],
            "phone_raw": [f"{phone_prefix}25570000{i}" for i in range(n)],
            "phone_number": [f"25570000{i}" for i in range(n)],
            "country_iso": ["TZ"] * n,
            "amount": [Decimal("1.5000")] * n,
        },
        schema=EVENTS.polars_schema(),
    )


@pytest.fixture
def events_contract() -> TableContract:
    return EVENTS


@pytest.fixture
def dim_contract() -> TableContract:
    return DIM
