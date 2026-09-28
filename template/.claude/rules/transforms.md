---
paths:
  - "src/**/transforms/**/*.py"
---

# Transforms (pure layer)

- Polars in, polars out. No I/O, no env, no DuckDB, no DBOS, no HTTP (`just imports` enforces this).
- Hamilton modules: each public function is a node; parameter names are the edges. No
  `from __future__ import annotations` in these modules.
- Parse types explicitly (`str.to_datetime(format, time_zone="UTC")`, `cast(pl.Int64, strict=True)`).
  Never let a reader infer types.
- The output node carries `@check_output_custom(PanderaPolars(CONTRACT.pandera_schema()))`.
- A cleaner exists once, as a polars expression. Measure dirt with `dirty_expr(col, cleaner)`; do not
  write a second "is dirty" predicate.
- Keep the raw input column (the witness) next to every normalized column.
- Business rules are `@check`s in `checks.py`, each with a live-fire test.
