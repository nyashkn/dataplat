"""Check primitives. Pure: polars in, Evidence out. No I/O.

- ``Verdict`` / ``Evidence``: PASS, FAIL, INCONCLUSIVE, with measured-vs-implied basis
- ``check``: register a named row check (a polars expression, True = valid)
- ``livefire``: prove a check catches the mutation it claims to catch
- ``dirty_expr`` / ``measure_dirt``: dirtiness is ``transform(v) != v``
- ``verify_derivation`` / ``verify_repair``: witness-column repairs
- ``profile``: rate gates with sample floors (below the floor is INCONCLUSIVE)
- ``Rate`` / ``rate_expr``: grain-safe ratios that are NULL, never a fake 0
- ``shape_class``: describe values without revealing them
"""

from dataplat.checks.dirty import dirty_expr, measure_dirt
from dataplat.checks.rate import Rate, rate_expr
from dataplat.checks.registry import Check, check, registered
from dataplat.checks.shapes import shape_class, shape_counts
from dataplat.checks.verdict import Evidence, Verdict, worst
from dataplat.checks.witness import verify_derivation, verify_repair

__all__ = [
    "Check",
    "Evidence",
    "Rate",
    "Verdict",
    "check",
    "dirty_expr",
    "measure_dirt",
    "rate_expr",
    "registered",
    "shape_class",
    "shape_counts",
    "verify_derivation",
    "verify_repair",
    "worst",
]
