"""Profile gates: rates with sample floors.

A rate computed on 4 rows proves nothing. Below ``min_rows`` a gate returns INCONCLUSIVE, not PASS.
Declare every threshold as a named constant in the module that enforces it, with a comment saying
where the number came from::

    MAX_NULL_PHONE_RATE = 0.35  # census 2026-09: 31% of telco rows have no phone; alert above 35%
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from dataplat.checks.verdict import Evidence, Verdict

MIN_ROWS_DEFAULT = 30

Gate = Callable[[pl.DataFrame], Evidence]


def _floor(name: str, df: pl.DataFrame, min_rows: int) -> Evidence | None:
    if df.height < min_rows:
        return Evidence(
            name,
            Verdict.INCONCLUSIVE,
            {"rows": df.height, "min_rows": min_rows},
            detail="sample below floor",
        )
    return None


def rows_at_least(n: int, *, name: str = "rows_at_least") -> Gate:
    def gate(df: pl.DataFrame) -> Evidence:
        return Evidence(name, Verdict.PASS if df.height >= n else Verdict.FAIL, {"rows": df.height, "min": n})

    gate.__name__ = name
    return gate


def null_rate_at_most(col: str, max_rate: float, *, min_rows: int = MIN_ROWS_DEFAULT) -> Gate:
    name = f"null_rate[{col}]<={max_rate}"

    def gate(df: pl.DataFrame) -> Evidence:
        if (e := _floor(name, df, min_rows)) is not None:
            return e
        rate = df[col].null_count() / df.height
        return Evidence(
            name, Verdict.PASS if rate <= max_rate else Verdict.FAIL, {"rows": df.height, "null_rate": round(rate, 6)}
        )

    gate.__name__ = name
    return gate


def share_at_least(label: str, expr: pl.Expr, min_share: float, *, min_rows: int = MIN_ROWS_DEFAULT) -> Gate:
    """Share of rows where ``expr`` is True (nulls count as False) must be at least ``min_share``."""
    name = f"share[{label}]>={min_share}"

    def gate(df: pl.DataFrame) -> Evidence:
        if (e := _floor(name, df, min_rows)) is not None:
            return e
        share = float(df.select(expr.fill_null(False).mean()).item())
        return Evidence(
            name, Verdict.PASS if share >= min_share else Verdict.FAIL, {"rows": df.height, "share": round(share, 6)}
        )

    gate.__name__ = name
    return gate
