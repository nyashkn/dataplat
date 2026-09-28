"""Live-fire: prove a check catches the failure it claims to catch.

A check that has never failed on bad data is only a claim. ``assert_catches`` runs three steps:

1. the clean fixture passes (otherwise the fixture or the check is wrong),
2. the mutation actually changes the fixture (otherwise the test proves nothing),
3. the mutated fixture fails.

Registered checks that pass step 3 are recorded; with ``pytest --livefire-strict`` (``just test-all``)
the session fails if any registered check was never live-fired.

The standard mutations come from real incidents: NULL to 0 (the fake-zero metric), +1e-6 (float
drift), a dropped row, a duplicated row, reordered rows, a leading ``+``, a ``.0`` float artifact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from dataplat.checks.registry import Check
from dataplat.checks.verdict import Evidence, Verdict

_FIRED: set[str] = set()


@dataclass(frozen=True)
class Mutation:
    name: str
    apply: Callable[[pl.DataFrame], pl.DataFrame] = field(repr=False)

    def __call__(self, df: pl.DataFrame) -> pl.DataFrame:
        return self.apply(df)


def _nth_non_null(df: pl.DataFrame, col: str, index: int) -> int:
    idx = df.with_row_index("__i").filter(pl.col(col).is_not_null())["__i"]
    if idx.len() <= index:
        raise ValueError(f"fixture has fewer than {index + 1} non-null values in {col!r}")
    return int(idx[index])


def _replace_at(df: pl.DataFrame, col: str, row: int, value: Any) -> pl.DataFrame:
    return (
        df.with_row_index("__i")
        .with_columns(
            pl.when(pl.col("__i") == row).then(pl.lit(value, dtype=df.schema[col])).otherwise(pl.col(col)).alias(col)
        )
        .drop("__i")
    )


def null_to_zero(col: str) -> Mutation:
    return Mutation(f"null_to_zero({col})", lambda df: df.with_columns(pl.col(col).fill_null(0)))


def nudge(col: str, eps: float = 1e-6, index: int = 0) -> Mutation:
    def f(df: pl.DataFrame) -> pl.DataFrame:
        row = _nth_non_null(df, col, index)
        return (
            df.with_row_index("__i")
            .with_columns(pl.when(pl.col("__i") == row).then(pl.col(col) + eps).otherwise(pl.col(col)).alias(col))
            .drop("__i")
        )

    return Mutation(f"nudge({col}, {eps})", f)


def drop_row(index: int = 0) -> Mutation:
    return Mutation(
        f"drop_row({index})",
        lambda df: df.with_row_index("__i").filter(pl.col("__i") != index).drop("__i"),
    )


def duplicate_row(index: int = 0) -> Mutation:
    return Mutation(f"duplicate_row({index})", lambda df: pl.concat([df, df.slice(index, 1)]))


def reorder_rows(seed: int = 7) -> Mutation:
    return Mutation(f"reorder_rows(seed={seed})", lambda df: df.sample(fraction=1.0, shuffle=True, seed=seed))


def prefix(col: str, text: str, index: int = 0) -> Mutation:
    def f(df: pl.DataFrame) -> pl.DataFrame:
        row = _nth_non_null(df, col, index)
        return _replace_at(df, col, row, text + str(df[col][row]))

    return Mutation(f"prefix({col}, {text!r})", f)


def suffix(col: str, text: str, index: int = 0) -> Mutation:
    def f(df: pl.DataFrame) -> pl.DataFrame:
        row = _nth_non_null(df, col, index)
        return _replace_at(df, col, row, str(df[col][row]) + text)

    return Mutation(f"suffix({col}, {text!r})", f)


def set_value(col: str, value: Any, index: int = 0) -> Mutation:
    return Mutation(f"set_value({col}, {value!r}, row={index})", lambda df: _replace_at(df, col, index, value))


def set_where(col: str, value: Any, where: pl.Expr, label: str = "") -> Mutation:
    return Mutation(
        f"set_where({col}, {value!r}{', ' + label if label else ''})",
        lambda df: df.with_columns(
            pl.when(where).then(pl.lit(value, dtype=df.schema[col])).otherwise(pl.col(col)).alias(col)
        ),
    )


# Short aliases for the incident-derived mutations.
def add_leading_plus(col: str, index: int = 0) -> Mutation:
    return prefix(col, "+", index)


def float_artifact(col: str, index: int = 0) -> Mutation:
    return suffix(col, ".0", index)


def _run(check: Check | Callable[[pl.DataFrame], Evidence], df: pl.DataFrame) -> Evidence:
    return check.evaluate(df) if isinstance(check, Check) else check(df)


def assert_catches(
    check: Check | Callable[[pl.DataFrame], Evidence], clean: pl.DataFrame, mutation: Mutation
) -> Evidence:
    name = check.name if isinstance(check, Check) else getattr(check, "__name__", "check")
    before = _run(check, clean)
    assert before.verdict is Verdict.PASS, (
        f"{name} does not pass on the clean fixture ({before}). Fix the fixture or the check first."
    )
    mutated = mutation(clean)
    assert not mutated.equals(clean, null_equal=True), (
        f"mutation {mutation.name} left the fixture unchanged, so it cannot exercise {name}. "
        "Pick a fixture row the mutation applies to."
    )
    after = _run(check, mutated)
    assert after.verdict is Verdict.FAIL, f"{name} did NOT catch {mutation.name}: {after}"
    if isinstance(check, Check):
        _FIRED.add(check.name)
    return after


def fired() -> frozenset[str]:
    return frozenset(_FIRED)
