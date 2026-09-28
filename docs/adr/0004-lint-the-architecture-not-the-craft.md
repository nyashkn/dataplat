# 0004. Lint the architecture, not the craft

- Status: accepted

## Context
Agents write almost all the code, sometimes in parallel. Prose rules drift. But a wall of style lints
slows agents and trains them to work around linters.

## Decision
Every rule is tiered: **auto** (formatter/autofix), **enforced** (a tool fails, and a live-fire test
proves it), or **judgment** (written down, reviewed). Enforced rules guard boundaries: the single writer,
no storage listing, layers, purity, no string dispatch, contract freshness, live-fired checks, the
duplication ratchet, secrets, and namespaces. Style beyond `ruff format` is not linted.

Checks run as early as their cost allows: edit hook (seconds), pre-commit (<10 s), pre-push, CI
(<5 min), nightly, box-only data checks. Duplication is a ratchet (`jscpd --baseline-from-ref main
--fail-on-new-clones 0`): existing clones are the baseline, new ones fail. The jscpd MCP server lets an
agent check a snippet before writing it.

## Evidence
`tests/architecture/test_gates.py` in every rendered project plants each violation and asserts its
gate fires. Writing these found two real bugs in the config: jscpd's `--fail-on-new-clones` swallowing
the path argument, and ruff ignoring `exclude` for explicit paths without `--force-exclude`.
