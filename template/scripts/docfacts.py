"""Facts the docs state, read from the code. Used by the Cog spans in README.md and docs/ARCHITECTURE.md.

Sorted output, no timestamps, so `cog --check` is stable. Add a function here for each fact a doc
repeats, then replace the sentence with a span (`just docs` rewrites them, `just docs-check` guards them).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from dataplat.lint import scan

ROOT = Path(__file__).resolve().parent.parent


def code_list(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def dpa_rules() -> list[str]:
    """Rule ids documented in the architecture scanner's module docstring (its registry)."""
    return sorted(re.findall(r"^(DPA\d{3})\s", scan.__doc__ or "", re.M))


def just_recipes() -> list[str]:
    out = subprocess.run(["just", "--summary"], check=True, capture_output=True, text=True, cwd=ROOT).stdout
    return sorted(out.split())
