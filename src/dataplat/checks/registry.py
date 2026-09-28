"""Named, registered row checks.

A check is a zero-argument function returning a polars expression that is True for VALID rows::

    @check(origin="issue #160")
    def v21_phone_no_leading_plus() -> pl.Expr:
        \"\"\"V21: phone_number never starts with '+'.\"\"\"
        return ~pl.col("phone_number").str.starts_with("+").fill_null(False)

Nulls in the result count as failures. Handle nulls on purpose with ``fill_null``; never let them pass
by accident. Registering a check makes it visible to the live-fire gate: ``just test-all`` fails if a
registered check was never shown to catch a bad row (see ``dataplat.checks.livefire``).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

import pandera.polars as pa
import polars as pl

from dataplat.checks.shapes import shape_counts
from dataplat.checks.verdict import Evidence, Verdict

_REGISTRY: dict[str, Check] = {}


@dataclass(frozen=True)
class Check:
    name: str
    build: Callable[[], pl.Expr] = field(repr=False)
    description: str = ""
    origin: str | None = None
    module: str = ""

    def expr(self) -> pl.Expr:
        return self.build()

    def columns(self) -> list[str]:
        return self.expr().meta.root_names()

    def evaluate(self, df: pl.DataFrame) -> Evidence:
        if df.height == 0:
            return Evidence(self.name, Verdict.INCONCLUSIVE, {"rows": 0}, detail="no rows to check")
        ok = df.select(self.expr().fill_null(False).alias("__ok"))["__ok"]
        failing = int((~ok).sum())
        measured: dict[str, object] = {"rows": df.height, "failing": failing}
        if failing:
            bad = df.filter(~ok)
            for c in self.columns():
                if c in bad.columns:
                    measured[f"shapes[{c}]"] = shape_counts(bad[c])
        return Evidence(
            self.name,
            Verdict.FAIL if failing else Verdict.PASS,
            measured,
            detail=self.description,
        )

    __call__ = evaluate

    def pandera(self) -> pa.Check:
        """This check as a dataframe-level pandera check (for contracts and Hamilton validators)."""
        build = self.build
        return pa.Check(
            lambda data: data.lazyframe.select(build().fill_null(False)),
            name=self.name,
            error=self.description or self.name,
        )


def check(
    fn: Callable[[], pl.Expr] | None = None, *, name: str | None = None, origin: str | None = None
) -> Check | Callable[[Callable[[], pl.Expr]], Check]:
    def wrap(f: Callable[[], pl.Expr]) -> Check:
        doc = inspect.getdoc(f) or ""
        c = Check(
            name=name or f.__name__,
            build=f,
            description=doc.strip().splitlines()[0] if doc.strip() else "",
            origin=origin,
            module=f.__module__,
        )
        prior = _REGISTRY.get(c.name)
        if prior is not None and prior.module != c.module:
            raise ValueError(
                f"check name {c.name!r} is registered twice ({prior.module} and {c.module}); "
                "reuse the existing check instead of re-declaring it"
            )
        _REGISTRY[c.name] = c
        return c

    return wrap(fn) if fn is not None else wrap


def registered() -> dict[str, Check]:
    return dict(_REGISTRY)
