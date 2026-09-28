"""Witness columns: every repair keeps the original.

A derived column (``phone_number``) is always computed from an untouched witness column (``phone_raw``)
that no repair may modify. Then:

- any row can be re-derived and checked: ``derived == derive(witness)``,
- a repair can be verified: only the derived column changed, and the witness is identical,
- a bad repair can be undone without a backup.

Tables whose derived columns have no witness cannot be repaired in place (``Writer.repair_partition``
refuses them).
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from dataplat.checks.verdict import Evidence, Verdict


def verify_derivation(
    df: pl.DataFrame, *, derived: str, witness: str, derive: Callable[[pl.Expr], pl.Expr]
) -> Evidence:
    """PASS iff ``derived == derive(witness)`` on every row (null-safe)."""
    name = f"derivation[{derived} <- {witness}]"
    if df.height == 0:
        return Evidence(name, Verdict.INCONCLUSIVE, {"rows": 0})
    mismatch = int(df.select((~pl.col(derived).eq_missing(derive(pl.col(witness)))).sum()).item())
    return Evidence(name, Verdict.FAIL if mismatch else Verdict.PASS, {"rows": df.height, "mismatch": mismatch})


def verify_repair(
    pre: pl.DataFrame,
    post: pl.DataFrame,
    *,
    repaired: str,
    witness: str,
    derive: Callable[[pl.Expr], pl.Expr],
) -> Evidence:
    """Row-aligned comparison of the pre-image and the repaired frame.

    PASS requires: same height and columns, witness identical, every other column identical, and the
    derivation holds on every row of ``post``.
    """
    name = f"repair[{repaired} <- {witness}]"
    problems: list[str] = []
    if pre.height != post.height:
        problems.append(f"row count changed {pre.height} -> {post.height}")
    if pre.columns != post.columns:
        problems.append("columns changed")
    if problems:
        return Evidence(
            name, Verdict.FAIL, {"rows_pre": pre.height, "rows_post": post.height}, detail="; ".join(problems)
        )
    untouched = [c for c in pre.columns if c != repaired]
    if not pre.select(untouched).equals(post.select(untouched), null_equal=True):
        changed_cols = [c for c in untouched if not pre[c].equals(post[c], null_equal=True)]
        problems.append(f"untouched columns changed: {changed_cols}")
    derivation = verify_derivation(post, derived=repaired, witness=witness, derive=derive)
    if derivation.verdict is not Verdict.PASS:
        problems.append(f"derivation fails on {derivation.measured.get('mismatch')} rows")
    changed = int((~pre[repaired].eq_missing(post[repaired])).sum())
    return Evidence(
        name,
        Verdict.FAIL if problems else Verdict.PASS,
        {"rows": pre.height, "changed": changed},
        detail="; ".join(problems),
    )
