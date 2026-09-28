# Lake census

Answers "what does the lake actually hold?" with counts from the run log, at one pinned snapshot.

## Steps

1. **Census** — Run `just census` and keep the snapshot id it prints.
   - tools: shell
   - output: {"type":"object","properties":{"snapshot":{"type":"integer"}}}
2. **Flag** — List every dataset with `missing_partitions > 0`, `zero_row_partitions > 0`, `lag_days > 2`
   or `code_versions > 1`. A mix of code versions usually means some partitions predate a fix.
   - tools: shell
3. **Agreement** — For each flagged dataset, compare the run log with the table
   (`dataplat.trust.deep_census`). Any mismatch means something wrote around the writer: stop and report.
   - tools: shell
   - on_failure: fail
4. **Report** — Post the table of flags with the snapshot id. Counts only; name no identifiers.
   - kind: checkpoint
   - terminal: true
