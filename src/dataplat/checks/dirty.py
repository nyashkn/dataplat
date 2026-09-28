"""Dirtiness is ``transform(v) != v``.

Do not maintain a separate "what counts as dirty" predicate that drifts from the cleaner. Use the cleaner
itself: a value is dirty exactly when cleaning changes it. That keeps the measurement and the repair in
agreement, so every run can report how much it cleaned.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from dataplat.checks.shapes import shape_counts
from dataplat.checks.verdict import Evidence, Verdict


def dirty_expr(col: str, clean: Callable[[pl.Expr], pl.Expr]) -> pl.Expr:
    """True where ``clean`` would change the value (null-safe: null -> null is clean)."""
    return ~pl.col(col).eq_missing(clean(pl.col(col)))


def measure_dirt(df: pl.DataFrame, col: str, clean: Callable[[pl.Expr], pl.Expr]) -> Evidence:
    """Counts and shape classes of the values ``clean`` would change. A measurement, not a gate."""
    if df.height == 0:
        return Evidence(f"dirt[{col}]", Verdict.INCONCLUSIVE, {"rows": 0})
    mask = df.select(dirty_expr(col, clean).alias("__d"))["__d"]
    dirty = df.filter(mask)
    after = dirty.select(clean(pl.col(col)).alias(col))[col]
    return Evidence(
        f"dirt[{col}]",
        Verdict.PASS,
        {
            "rows": df.height,
            "dirty": dirty.height,
            "dirty_rate": round(dirty.height / df.height, 6),
            "shapes_before": shape_counts(dirty[col]),
            "shapes_after": shape_counts(after),
        },
    )
