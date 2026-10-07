# Traps

Real incidents behind the rules. When a new one happens, add it here and link it from the rule that now
prevents it.

| Trap | What happened | Now prevented by |
|---|---|---|
| **Seven writers** | Parquet was written from seven places (forward, payments ×3, backfill, fx ×2, dims, content gaps), each with its own S3 helpers and atomic-move logic. They drifted. | Single `Writer` (rule 2), DPA001/002/004 |
| **Listing truncation** | RustFS returns about 501 entries per directory listing. Code that discovered partitions by listing silently missed history. | Catalog lookups, DPA003 |
| **String dispatch blindness** | `usage.recon` resolved entrypoints from strings. Static analysis saw no importers, so two audits and desloppify flagged 99 of 146 live files as dead. | TID251 on `import_module`, argparse bound to functions (rule 6) |
| **Near-deletion via `final_vars`** | Hamilton outputs requested by name (`"normalized_good"`) looked unused and were nearly deleted. | function-object outputs, DPA006, `run_dag` TypeError |
| **Validators nobody ran** | V6/V21/V24 (`backfill/schema.py:validate`) were called only by a test. Production never ran them. | checks bound to contracts + validated at every commit + live-fire gate (rule 10) |
| **Leading plus** | Phones arrived as `+255…` (#160, #176) and split identities. | V21 + `normalize_phone` |
| **Float artifacts** | About 3.83M rows in 2023-08/09 had phones shaped `9{12}.9`, a digit string with a trailing `.0` (#197), after a spreadsheet round-trip. | V24 + normalization, dirt measured on every run |
| **20 zero-row days** | `fact_charges_topline` (generation 2 of 3) had 20 days with zero rows and no writer anyone could find. | writer refuses empty partitions without a note; census flags zero-row days |
| **Grain trap** | Summing quote revisions (not quotes) turned a $10k total into $500k. | `dataplat.grain` in contracts, `Rate`, new-metric skill |
| **Fake zero** | A metric with missing inputs showed 0, read as "nothing happened". | Blocked-metric rule: NULL / Refusal |
| **Clock-ordered cron** | Jobs ran in clock order, not dependency order (#115), so downstream read yesterday's upstream. | run-log preconditions |
| **Identity ceiling** | `fact_event.user_id` holds three encodings; only 3.46% joins, and 36% of digit-shaped rows match. | documented in project-facts; no silent joins |
| **ISRC is not a key** | 62,338 distinct ISRCs across 68,074 rows, up to 9 songs per code. | ontology says so; grain declared |
| **Constant weight** | `download_or_stream_count` is always 1, so summing it looks like weighting but isn't. | contract bounds (min = max = 1): a change fails loudly |
| **Static feed can't serve history** | `dls_daily.csv` / `payment_daily.csv` are snapshots; history can't be rebuilt from them. | frozen history registered from manifests |
| **Icebug silent empties** | Icebug-backed LadybugDB tables returned `[]` for backward and two-hop patterns (real_ladybug 0.15.3, icebug-format 1.1.0). | native graph files + SQL/Cypher parity gate |
| **Extension host blocked** | `extensions.duckdb.org` returned 403 under the egress policy. | DuckDB extensions pinned as PyPI wheels, loaded by path |
