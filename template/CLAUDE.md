@AGENTS.md

## Claude Code specifics

- Every edit is formatted and scanned by `.claude/hooks/post_edit.py`. When it reports something, fix it
  before moving on.
- Bash commands that expose secrets or skip gates are blocked by `.claude/hooks/guard_bash.py`. Use
  `just` recipes; they load `.env` themselves.
- Skills: `new-dataset`, `new-metric`, `lake-ops`, `investigate` (in `.claude/skills/`).
- Subagent `lake-ops` runs lake operations in its own worktree.
- Path rules in `.claude/rules/` load automatically when you work in `transforms/`, `pipelines/` or
  `contracts/`.
- Guardrail files (ruff.toml, .importlinter, .jscpd.json, justfile, .github/, .claude/, AGENTS.md)
  ask for approval. Propose the change and explain why; don't route around it.
