---
name: investigate
description: Investigate a data question or anomaly rigorously (why did X drop, is Y correct, can we trust Z). Use before claiming anything about the data.
---

# Investigate data

Follow `agents/sops/data-investigation/SOP.md`. The short version:

1. **Pin a snapshot.** `with connect(read_only=True).reader() as r:` and quote `r.snapshot` next to every number.
2. **Measure, don't infer.** Tag each claim **measured** (query + snapshot) or **implied** (read from
   code or docs). Never let an implied claim pass as a measured one.
3. **Counts, rates, shape classes.** `dataplat.checks.shape_counts(series)` describes values without
   revealing them. Raw identifiers never go into a reply, a log or a file.
4. **Dirtiness = transform(v) != v.** Measure how many values the cleaner would change before arguing
   about whether data is "dirty".
5. **Live-fire your own conclusion.** If you claim "column X is never NULL", run the check on a frame
   where you set one value to NULL and confirm it fails.
6. **Inconclusive is an answer.** Too few rows, a missing partition, or two readings the data can't
   separate: say so and name what would decide it.
7. Record anything that surprised you in `docs/friction-log.md`.
