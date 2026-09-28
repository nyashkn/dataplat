---
name: lake-ops
description: Operate the lake - census, backfill a date range, repair a column from its witness, restore a partition, register frozen legacy files. Use for "what's in the lake", "backfill", "re-run", "fix the data", "migrate history".
---

# Lake operations

These procedures are shared with non-Claude agents, so each one lives in exactly one place, as a
framework-neutral SOP under `agents/sops/`. Follow the SOP; do not improvise a different method.

| Task | SOP |
|---|---|
| What does the lake hold? gaps, zero-row days, lag | `agents/sops/lake-census/SOP.md` |
| Backfill or re-run partitions | `agents/sops/lake-backfill/SOP.md` |
| Repair a derived column from its witness | `agents/sops/lake-repair/SOP.md` |
| Adopt frozen legacy Parquet without rewriting | `agents/sops/lake-register-frozen/SOP.md` |

Always:
- Report counts, rates and shape classes only. Never a raw identifier, never to a file.
- Say what you **measured** (command + snapshot id) and what you only **infer** from code.
- Stop and ask before any production write. Production writes run on the box, by the owner, through
  `just` recipes.
