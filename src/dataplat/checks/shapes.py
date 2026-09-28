"""Shape classes: describe values without revealing them.

``+255700000000.0`` becomes ``+9{12}.9``; ``BD_TELCO_X`` becomes ``A{2}_A{5}_A``. Reports, check
failures and agent answers use shapes and counts only. A raw identifier never goes into a message,
a log line or a file.
"""

from __future__ import annotations

from collections import Counter

import polars as pl

MAX_DISTINCT_FOR_SHAPES = 10_000


def _cls(ch: str) -> str:
    if ch.isdigit():
        return "9"
    if ch.isascii() and ch.isalpha():
        return "A" if ch.isupper() else "a"
    if ch.isalpha():
        return "U"  # non-ascii letter
    if ch.isspace():
        return "_" if ch == " " else "\\s"
    return ch


def shape_class(value: object) -> str:
    if value is None:
        return "<null>"
    s = str(value)
    if s == "":
        return "<empty>"
    out: list[str] = []
    prev, run = "", 0
    for c in map(_cls, s):
        if c == prev:
            run += 1
            continue
        if prev:
            out.append(prev if run == 1 else f"{prev}{{{run}}}")
        prev, run = c, 1
    out.append(prev if run == 1 else f"{prev}{{{run}}}")
    return "".join(out)


def shape_counts(values: pl.Series, top: int = 5) -> dict[str, int]:
    """Top shape classes with row counts. Works on the top ``MAX_DISTINCT_FOR_SHAPES`` distinct values."""
    if values.len() == 0:
        return {}
    vc = values.value_counts(sort=True).head(MAX_DISTINCT_FOR_SHAPES)
    col, cnt = vc.columns[0], vc.columns[1]
    counter: Counter[str] = Counter()
    for v, n in zip(vc[col].to_list(), vc[cnt].to_list(), strict=True):
        counter[shape_class(v)] += int(n)
    return dict(counter.most_common(top))
