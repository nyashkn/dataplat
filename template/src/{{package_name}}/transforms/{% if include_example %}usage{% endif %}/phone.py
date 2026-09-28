"""Phone normalization, ported from mdundo-pipeline ``src/_shared/phone.py:normalize_phone``.

``normalize_phone`` (scalar) is the readable specification; ``normalize_phone_expr`` (polars) is what
pipelines run. ``tests/unit/transforms/usage/test_phone.py`` proves they agree on a synthetic corpus
covering every shape seen in production (leading +, ``.0`` float artifacts, ``00`` prefixes, hex ids).

Rules:
- blank, "nan" or missing becomes None
- surrounding whitespace is stripped, then one leading "+" is dropped
- a trailing integral-float artifact (``255700000000.0``) is removed
- two or more leading zeros on an all-digit value are removed; a single leading zero is kept
- anything else (hex identifiers on BD telco rows) is left untouched
"""

import re

import polars as pl

_FLOAT_ARTIFACT = re.compile(r"^(\d+)\.0+$")


def normalize_phone(user_phone: str | None) -> str | None:
    if user_phone is None:
        return None
    s = str(user_phone).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.removeprefix("+")
    if m := _FLOAT_ARTIFACT.match(s):
        s = m.group(1)
    if s.isdigit() and s.startswith("00"):
        s = s.lstrip("0")
    return s or None


def normalize_phone_expr(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars()
    s = pl.when(s.is_null() | (s == "") | (s.str.to_lowercase() == "nan")).then(None).otherwise(s)
    s = s.str.strip_prefix("+")
    s = pl.when(s.str.contains(r"^\d+\.0+$")).then(s.str.replace(r"\.0+$", "")).otherwise(s)
    s = pl.when(s.str.contains(r"^00\d*$")).then(s.str.strip_chars_start("0")).otherwise(s)
    return pl.when(s == "").then(None).otherwise(s)
