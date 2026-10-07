"""Rates that aggregate correctly and never fake a zero.

The grain trap: averaging per-group ratios, or summing a column at the wrong grain, turns $10k into
$500k. A ``Rate`` keeps its numerator and denominator, so combining rates sums both parts first.

The Blocked Metrics rule: when the denominator is 0 the value is NULL (``None``), never 0. A zero would
claim "we measured nothing happening"; NULL says "we could not measure".
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class Rate:
    num: float
    den: float

    @property
    def value(self) -> float | None:
        return None if self.den == 0 else self.num / self.den

    def __add__(self, other: Rate) -> Rate:
        return Rate(self.num + other.num, self.den + other.den)

    @classmethod
    def of(cls, df: pl.DataFrame, num: pl.Expr, den: pl.Expr) -> Rate:
        n, d = df.select(num.sum().alias("n"), den.sum().alias("d")).row(0)
        return cls(float(n or 0), float(d or 0))


def rate_expr(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    """Row-wise ratio that is NULL (not 0, not inf) when the denominator is 0 or NULL."""
    return pl.when(den.fill_null(0) != 0).then(num / den).otherwise(None)
