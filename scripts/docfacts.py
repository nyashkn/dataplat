"""Facts the docs state, read from the code. Used by the Cog spans in README.md.

No parsing of our own beyond reading registries; output is sorted and has no timestamps, so
`cog --check` is stable.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import tomllib
from pathlib import Path

from dataplat import cli
from dataplat.lint import scan

ROOT = Path(__file__).resolve().parent.parent


def code_list(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def dpa_rules() -> list[str]:
    """Rule ids documented in the scanner's module docstring (its registry)."""
    return sorted(re.findall(r"^(DPA\d{3})\s", scan.__doc__ or "", re.M))


def cli_subcommands() -> list[str]:
    """Subcommands, from argparse's own usage line (`{a,b,c}`)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        cli.main(["--help"])
    m = re.search(r"\{([\w,-]+)\}", out.getvalue())
    return sorted(m.group(1).split(",")) if m else []


def extras() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return sorted(data["project"]["optional-dependencies"])


def just_recipes() -> list[str]:
    out = subprocess.run(["just", "--summary"], check=True, capture_output=True, text=True, cwd=ROOT).stdout
    return sorted(out.split())
