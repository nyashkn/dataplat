#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write/MultiEdit: format, autofix, then report what is left.

Runs on every Python file an agent touches, so a violation shows up seconds after it is written,
not at PR time:
  1. ruff format + ruff check --fix   (silent autofixes)
  2. ruff check                        (what autofix could not fix, e.g. banned APIs)
  3. dataplat lint                     (architecture rules DPA001-DPA008)
Exit code 2 sends the findings back to the agent. Stdlib only; tools come from the project venv.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def tool(project: Path, name: str) -> list[str]:
    venv = project / ".venv" / "bin" / name
    if venv.exists():
        return [str(venv)]
    if shutil.which("uv"):
        return ["uv", "run", "--frozen", name]
    return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = Path(str(payload.get("tool_input", {}).get("file_path", "")))
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    if path.suffix != ".py" or not path.exists() or "_generated" in path.parts:
        return 0
    ruff, dataplat = tool(project, "ruff"), tool(project, "dataplat")
    if not ruff:
        return 0
    run = lambda cmd: subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=60)  # noqa: E731
    run([*ruff, "format", "--quiet", str(path)])
    run([*ruff, "check", "--fix", "--quiet", str(path)])
    problems: list[str] = []
    rc = run([*ruff, "check", "--output-format", "concise", str(path)])
    if rc.returncode != 0:
        problems.append(rc.stdout.strip())
    if dataplat and "src" in path.parts:
        dl = run([*dataplat, "lint", str(path)])
        if dl.returncode != 0:
            problems.append(dl.stdout.strip())
    if problems:
        print(
            "Fix before moving on (see docs/CONVENTIONS.md for each rule):\n" + "\n".join(problems),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
