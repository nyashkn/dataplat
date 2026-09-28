"""Table contracts: the physical shape of every lake table, declared once.

A contract is generated from LinkML (``contracts/tables.yaml``) into a Python module of constants
(``contracts/_generated/tables.py``). Code refers to tables through those constants, never by name
strings, so a rename breaks at import time and code search shows every use.

Frames are checked strictly: exact column names, order and dtypes. Nothing is coerced. A transform
produces the right types on purpose; the writer never "fixes" them silently.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

import pandera.errors as pae
import pandera.polars as pa
import polars as pl

from dataplat.checks.pandera_errors import summarize
from dataplat.errors import ContractError

if TYPE_CHECKING:
    from dataplat.checks.registry import Check

_SIMPLE: dict[str, tuple[pl.DataType, str]] = {
    "string": (pl.String(), "VARCHAR"),
    "int64": (pl.Int64(), "BIGINT"),
    "int32": (pl.Int32(), "INTEGER"),
    "int16": (pl.Int16(), "SMALLINT"),
    "float64": (pl.Float64(), "DOUBLE"),
    "bool": (pl.Boolean(), "BOOLEAN"),
    "date": (pl.Date(), "DATE"),
    "datetime_utc": (pl.Datetime("us", "UTC"), "TIMESTAMPTZ"),
    "datetime": (pl.Datetime("us"), "TIMESTAMP"),
}
_DECIMAL = re.compile(r"^decimal\((\d+),\s*(\d+)\)$")
DTYPES = tuple(_SIMPLE) + ("decimal(p,s)",)


def resolve_dtype(name: str) -> tuple[pl.DataType, str]:
    if name in _SIMPLE:
        return _SIMPLE[name]
    if m := _DECIMAL.match(name):
        p, s = int(m.group(1)), int(m.group(2))
        return pl.Decimal(p, s), f"DECIMAL({p},{s})"
    raise ContractError(f"unknown dtype {name!r}; use one of {', '.join(DTYPES)}")


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    required: bool = False
    pattern: str | None = None
    allowed: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    @property
    def polars(self) -> pl.DataType:
        return resolve_dtype(self.dtype)[0]

    @property
    def duckdb(self) -> str:
        return resolve_dtype(self.dtype)[1]


@dataclass(frozen=True)
class TableContract:
    name: str
    table: str  # "<schema>.<table>" inside the lake
    columns: tuple[Column, ...]
    partition_by: str | None = None
    grain: tuple[str, ...] = ()  # empty = event log (every row is one event; no key)
    cadence: str | None = None  # "daily" | None
    expected_start: str | None = None  # ISO date of the first expected partition
    description: str = ""
    checks: tuple[Check, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ContractError(f"{self.name}: duplicate column names")
        for key in ((self.partition_by,) if self.partition_by else ()) + self.grain:
            if key not in names:
                raise ContractError(f"{self.name}: {key!r} is not a column")
        if self.table.count(".") != 1:
            raise ContractError(f"{self.name}: table must be '<schema>.<table>', got {self.table!r}")
        for c in self.columns:
            resolve_dtype(c.dtype)

    @property
    def schema_name(self) -> str:
        return self.table.split(".")[0]

    @property
    def table_name(self) -> str:
        return self.table.split(".")[1]

    def qualified(self, catalog: str = "lake") -> str:
        return f"{catalog}.{self.schema_name}.{self.table_name}"

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def polars_schema(self) -> pl.Schema:
        return pl.Schema({c.name: c.polars for c in self.columns})

    def ddl_columns(self) -> str:
        return ", ".join(f'"{c.name}" {c.duckdb}{" NOT NULL" if c.required else ""}' for c in self.columns)

    def fingerprint(self) -> str:
        """Stable hash of the physical shape (not the bound checks). Recorded on every commit."""
        payload = {k: v for k, v in asdict(self).items() if k != "checks"}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def with_checks(self, *checks: Check) -> TableContract:
        return replace(self, checks=self.checks + tuple(checks))

    def empty(self) -> pl.DataFrame:
        return pl.DataFrame(schema=self.polars_schema())

    def pandera_schema(self, *extra: Check) -> pa.DataFrameSchema:
        cols = {}
        for c in self.columns:
            checks: list[pa.Check] = []
            if c.pattern:
                checks.append(pa.Check.str_matches(c.pattern))
            if c.allowed:
                checks.append(pa.Check.isin(list(c.allowed)))
            if c.minimum is not None:
                checks.append(pa.Check.ge(c.minimum))
            if c.maximum is not None:
                checks.append(pa.Check.le(c.maximum))
            cols[c.name] = pa.Column(c.polars, checks=checks, nullable=not c.required)
        return pa.DataFrameSchema(
            cols,
            checks=[chk.pandera() for chk in (*self.checks, *extra)],
            unique=list(self.grain) or None,
            strict=True,
            ordered=True,
            coerce=False,
            name=self.name,
        )

    def schema_diff(self, schema: pl.Schema) -> list[str]:
        want = self.polars_schema()
        diffs: list[str] = []
        missing = [c for c in want if c not in schema]
        extra = [c for c in schema if c not in want]
        if missing:
            diffs.append(f"missing columns {missing}")
        if extra:
            diffs.append(f"unexpected columns {extra}")
        for c in want:
            if c in schema and schema[c] != want[c]:
                diffs.append(f"{c}: dtype {schema[c]} != contract {want[c]}")
        if not missing and not extra and list(schema) != list(want):
            diffs.append(f"column order {list(schema)} != contract {list(want)}")
        return diffs

    def validate(self, df: pl.DataFrame) -> None:
        """Raise ContractError unless ``df`` matches exactly and passes every value check."""
        if diffs := self.schema_diff(df.schema):
            raise ContractError(f"{self.name} ({self.table}): " + "; ".join(diffs))
        try:
            self.pandera_schema().validate(df, lazy=True)
        except (pae.SchemaErrors, pae.SchemaError) as err:
            message, _ = summarize(err, f"{self.name} ({self.table})")
            raise ContractError(message) from None
