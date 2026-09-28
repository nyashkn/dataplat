# Don'ts

Each item gives what to do instead. Most are enforced (see `CONVENTIONS.md`); the rest need judgment.

**Storage**
- Don't write Parquet, CSV or Delta outside `pipelines/` (`.write_parquet`, `pq.write_table`,
  `COPY ... TO`). Instead: `Writer.commit_partition` / `replace` / `append` in a DBOS step.
- Don't list buckets or directories to find data (`os.listdir`, `glob`, `.rglob`, `fs.ls`,
  `read_parquet('.../*')`). Instead: `Lake.files(contract)`, or a manifest from the source system.
- Don't read lake files directly (`pl.read_parquet`). Instead: `lake.reader().scan(contract)`.
- Don't commit an empty partition to "mark the day done". Instead: fix the source; if it truly had no
  rows, use `allow_empty=True, note="why"`.
- Don't `ALTER` a table to make a frame fit. Instead: change the contract and write a migration.

**Code shape**
- Don't dispatch on strings (`import_module(f"...{name}")`, `getattr(module, name)`, `final_vars=["x"]`).
  Instead: import the function and pass it.
- Don't do I/O in `transforms/`. Instead: have `sources/` return frames and `pipelines/` commit them.
- Don't let a reader infer types (`infer_schema=True`, pandas defaults). Instead: read strings, parse
  explicitly.
- Don't write a second "is this dirty?" predicate. Instead: `dirty_expr(col, cleaner)`, where dirty means
  `cleaner(v) != v`.
- Don't add a helper before searching for one. Instead: code search + jscpd `check_duplication`.
- Don't use `from __future__ import annotations` in Hamilton modules.

**Numbers**
- Don't return 0 when you couldn't measure. Instead: NULL / `Refusal` / `Rate(...).value is None`.
- Don't average ratios. Instead: `Rate(sum(num), sum(den))`.
- Don't `SUM` before checking the grain. Instead: read `dataplat.grain`; dedupe to the grain first.
- Don't define a measure twice. Instead: one semantic table owns it; everything else imports it.
- Don't answer an agent's question outside the gate. Instead: `dataplat.gate.answer(...)` with
  preconditions.

**Data handling**
- Don't print, log or save raw identifiers (phones, user ids, national ids). Instead: counts, rates,
  `shape_counts`.
- Don't copy production rows into tests or fixtures. Instead: generate synthetic rows with the same
  shapes (`tests/fixtures/synthetic.py`).
- Don't repair a column in place without its witness. Instead: `Writer.repair_partition(..., witness=...)`.
- Don't claim what you didn't measure. Instead: label it *implied* and say how to measure it.
- Don't put values in exception messages (`f"bad phone {phone}"`). Telemetry scrubs them, but the
  message also lands in DBOS's own tables. Instead: `f"bad phone, shape {shape_class(phone)}"`.

**Process**
- Don't commit to `main`, force-push, or skip hooks (`--no-verify`). Instead: a worktree branch and a PR.
- Don't loosen a guardrail to get green. Instead: propose the change to the owner, with the reason.
- Don't run data checks in CI or point CI at the lake. Instead: `just verify-prod` on the box.
- Don't read `.env`, dump the environment or list secrets. Instead: `just` recipes load what they need.
- Don't share a lake namespace between parallel agents. Instead: `just worktree x`.
- Don't approve an action, even your own. Instead: propose it and tell the owner the workflow id.
