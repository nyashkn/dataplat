"""Turn pandera failures into messages safe to show an agent: counts and shape classes, no raw values."""

from __future__ import annotations

from typing import Any

import pandera.errors as pae
import polars as pl

from dataplat.checks.shapes import shape_counts


def summarize(err: pae.SchemaErrors | pae.SchemaError, schema_name: str = "") -> tuple[str, dict[str, Any]]:
    fc = getattr(err, "failure_cases", None)
    if not isinstance(fc, pl.DataFrame) or fc.height == 0:
        # schema-level errors (wrong dtype, missing column) carry no row values
        msg = str(err).splitlines()[0] if str(err) else type(err).__name__
        return f"{schema_name}: {msg}", {"errors": 1}
    groups: dict[str, dict[str, Any]] = {}
    col_key = "column" if "column" in fc.columns else None
    for key, sub in fc.group_by([c for c in (col_key, "check") if c], maintain_order=True):
        label = " / ".join(str(k) for k in key if k is not None)
        groups[label] = {
            "rows": sub.height,
            "shapes": shape_counts(sub["failure_case"].cast(pl.String)) if "failure_case" in sub.columns else {},
        }
    parts = [f"{label}: {g['rows']} rows, shapes {g['shapes']}" for label, g in groups.items()]
    return f"{schema_name} failed {len(groups)} check(s): " + "; ".join(parts), groups
