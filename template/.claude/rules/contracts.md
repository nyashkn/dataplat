---
paths:
  - "src/**/contracts/**"
---

# Contracts

- Edit the LinkML (`tables.yaml`, `ontology.yaml`), then run `just contracts`. Never edit `_generated/`;
  CI fails when it is stale.
- Code refers to tables by generated constant (`tables.USAGE_EVENT`), never by name string.
- Money is `decimal(p,s)` via `annotations: {dataplat.dtype: "decimal(18,4)"}`. Never a float.
- Declare `dataplat.grain`. `none` means event log: rows are events and there is no key.
- A changed column type on an existing table is a migration (write one in `pipelines/`), not an edit.
  The writer refuses drifted tables.
- Restricted fields (`[tool.dataplat] forbidden_columns`) cannot be declared. Drop them at the source.
