"""Namespaces: how parallel agents share one box without sharing state.

A namespace is the unit of isolation. Each one gets its own
  - DuckLake metadata schema (Postgres catalog) or catalog file (DuckDB catalog),
  - data prefix (``<data_path>/_ns/<namespace>/``),
  - DBOS application name and queue prefix.

Resolution order: ``DATAPLAT_NS`` env var, else the git worktree directory name when running inside a
linked worktree, else ``main``. A linked worktree may never resolve to ``main``: that is the rule that
stops an agent in ``.trees/feat-x`` from overwriting production partitions.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from dataplat.errors import IsolationError

MAIN = "main"
_VALID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def sanitize(name: str) -> str:
    """Turn a branch/worktree name into a namespace: lowercase, [a-z0-9_], starts with a letter."""
    ns = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not ns or not ns[0].isalpha():
        ns = f"ns_{ns}".rstrip("_")
    return ns[:40]


def validate(ns: str) -> str:
    if not _VALID.match(ns):
        raise IsolationError(
            f"invalid namespace {ns!r}: use lowercase letters, digits and underscores, "
            "starting with a letter (max 40 chars)"
        )
    return ns


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def linked_worktree_name(cwd: Path | None = None) -> str | None:
    """Name of the linked git worktree containing ``cwd``; None for the primary checkout or no git."""
    here = Path(cwd or Path.cwd())
    git_dir = _git(["rev-parse", "--absolute-git-dir"], here)
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], here)
    if not git_dir or not common or Path(git_dir) == Path(common):
        return None
    top = _git(["rev-parse", "--show-toplevel"], here)
    return Path(top).name if top else None


def current_namespace(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
    env = os.environ if env is None else env
    worktree = linked_worktree_name(cwd)
    explicit = env.get("DATAPLAT_NS", "").strip()
    ns = validate(explicit) if explicit else (sanitize(worktree) if worktree else MAIN)
    if worktree and ns == MAIN:
        raise IsolationError(
            f"worktree {worktree!r} resolved to namespace 'main'. Worktrees get their own "
            "namespace so parallel agents cannot overwrite each other's (or production's) "
            "partitions. Unset DATAPLAT_NS, or set it to a non-main value."
        )
    return ns


def data_prefix(data_path: str, ns: str) -> str:
    root = data_path if data_path.endswith("/") else data_path + "/"
    return root if ns == MAIN else f"{root}_ns/{ns}/"


def queue_name(base: str, ns: str) -> str:
    """DBOS queue name for this namespace (queues are process-global; prefixing keeps them apart)."""
    return base if ns == MAIN else f"{ns}.{base}"
