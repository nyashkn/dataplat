## What and why
<!-- One paragraph. Link the issue, trap (docs/traps.md) or friction-log entry this addresses. -->

## Evidence (paste output, not claims)
- [ ] `just ci`: last lines
  ```
  ```
- [ ] New or changed checks are live-fired. Test names:
- [ ] Pipelines changed: `just census` before and after (counts only)
  ```
  ```
- [ ] Every data claim in this PR says **measured** (command + snapshot id) or **implied** (read from code)

## Boundaries touched
- [ ] contracts (LinkML): regenerated with `just contracts`, `_generated/` committed
- [ ] new dataset: followed `.claude/skills/new-dataset`
- [ ] guardrail config (ruff.toml, .importlinter, .jscpd.json, .pre-commit-config.yaml, .github/, .claude/, justfile): owner review required
