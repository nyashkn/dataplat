# SOPs

Standard operating procedures for this project's agents. They are framework-neutral Markdown in the
ZeroClaw layout (`<name>/SOP.toml` + `<name>/SOP.md`, steps under `## Steps`), so:

- ZeroClaw runs them directly: point `sop.sops_dir` at this directory, or link it into `shared/sops`,
  then check with `zeroclaw sop validate`.
- Claude Code reaches them through `.claude/skills/lake-ops` and `.claude/skills/investigate`, which
  point here instead of copying them.

Every SOP ends in evidence: the commands run, the snapshot ids, and counts only.
