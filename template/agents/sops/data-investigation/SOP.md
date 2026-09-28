# Data investigation

## Steps

1. **Pin** — Open `connect(read_only=True).reader()` and note `reader.snapshot`. Every number you report
   cites it.
   - tools: shell
2. **Separate** — Write down what the code *implies* (read from transforms and contracts) apart from
   what you will *measure*. Implied claims are hypotheses, not findings.
3. **Measure** — Answer with counts, rates and `shape_counts` only. Dirtiness is
   `transform(v) != v`. A number with a sample below its floor is INCONCLUSIVE.
   - tools: shell
4. **Live-fire the conclusion** — If you conclude "X never happens", mutate a copy of the data so X
   happens and show your query detects it. If it doesn't, the conclusion is unproven.
   - tools: shell
5. **Report** — Findings as measured / implied / inconclusive, each with its snapshot id and command.
   Add anything surprising to `docs/friction-log.md`. No raw identifiers anywhere.
   - kind: checkpoint
   - terminal: true
