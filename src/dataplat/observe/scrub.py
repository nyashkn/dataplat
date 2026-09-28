"""Keep identifiers out of telemetry.

Exception messages travel with every failed span and error log. dataplat code writes counts and shape
classes into messages, never raw values, but a message from polars, DuckDB or a database driver can
still quote one. ``scrub`` is the backstop applied to everything a process exports:

- email addresses become ``<email>``; UUIDs become ``<uuid>`` (they may be someone's id; workflow ids
  travel as span attributes instead);
- quoted literals that contain a digit become their shape (``'12a'`` -> ``'9{2}a'``), which is where
  library errors put the values they failed on. Paths and literals that are already shapes are left
  alone;
- phone numbers written in groups (``0712 345 678``, 9+ digits) become their shape;
- hex tokens of 24+ characters (keys, hashes) become ``x{n}``;
- runs of 7+ digits with an optional ``+`` and decimal tail (phones, national IDs, payout and account
  numbers) become their shape: ``+254712345678`` -> ``+9{12}``, ``254712345678.0`` -> ``9{12}.9``.

Dates, times, small counts, column names, paths and workflow names pass through: they carry the
meaning of an error. ``scrub_stacktrace`` keeps frame headers and source lines (code, not data) readable
and applies the full rules to the exception lines. ``error_signature`` goes further for grouping: hex ids
become ``<hex>`` and every free-standing number becomes ``#``, so the same failure on two days, or in two
workflows, is one signature (``partition #-#-# missing``).
"""

from __future__ import annotations

import re

from dataplat.checks.shapes import shape_class

MAX_MESSAGE = 2000
MAX_STACKTRACE = 16_000
MAX_SIGNATURE = 200

_UUID = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_QUOTED = re.compile(r"(['\"`])([^'\"`\n]{0,120}?\d[^'\"`\n]{0,120}?)\1")
_GROUPED_PHONE = re.compile(r"(?<![\w+])\+?\d{2,4}(?: \d{2,4}){2,4}(?!\w)")
_LONG_HEX = re.compile(r"\b[0-9A-Fa-f]{24,}\b")
_LONG_DIGITS = re.compile(r"\+?\d{7,}(?:\.\d+)?")
_HEX_ID = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b")
# numbers that stand alone: not part of a word (INT32) nor part of a shape (9{12})
_DIGITS = re.compile(r"(?<![A-Za-z{\d])\d+(?![A-Za-z\d]*\})(?!\{)")
_SPACES = re.compile(r"\s+")


def _grouped_phone(m: re.Match[str]) -> str:
    text = m.group()
    return shape_class(text) if sum(c.isdigit() for c in text) >= 9 else text


def _quoted(m: re.Match[str]) -> str:
    quote, literal = m.group(1), m.group(2)
    if "{" in literal or "/" in literal or "\\" in literal:  # a shape already, or a path
        return m.group()
    return f"{quote}{shape_class(literal)}{quote}"


def _scrub(text: str, *, quoted: bool) -> str:
    out = _UUID.sub("<uuid>", text)
    out = _EMAIL.sub("<email>", out)
    if quoted:
        out = _QUOTED.sub(_quoted, out)
    out = _GROUPED_PHONE.sub(_grouped_phone, out)
    out = _LONG_HEX.sub(lambda m: f"x{{{len(m.group())}}}", out)
    return _LONG_DIGITS.sub(lambda m: shape_class(m.group()), out)


def scrub(text: str) -> str:
    """``text`` with identifiers replaced by their shapes. Idempotent."""
    if not text:
        return text
    return _scrub(text, quoted=True)[:MAX_MESSAGE]


def scrub_stacktrace(text: str) -> str:
    """A Python traceback with identifiers removed from its exception lines. Keeps the end if long."""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        code = line.startswith(("  File ", "    "))  # frame header or source line: code, not data
        lines.append(_scrub(line, quoted=not code))
    return "\n".join(lines)[-MAX_STACKTRACE:]


def error_signature(message: str) -> str:
    """A stable key for grouping failures: scrubbed, numbers replaced by ``#``, first line only."""
    if not message:
        return ""
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    out = _HEX_ID.sub("<hex>", scrub(first_line))
    out = _DIGITS.sub("#", out)
    return _SPACES.sub(" ", out).strip()[:MAX_SIGNATURE]
