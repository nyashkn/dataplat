# 0003. Contracts are LinkML, generated into constants

- Status: accepted

## Decision
Physical tables are LinkML classes annotated with `dataplat.table`, `partition_by`, `grain`, `cadence`,
`expected_start`. `dataplat contracts gen` writes `_generated/tables.py` (one `TableContract` constant
per table), plus Pydantic models and JSON Schema for the ontology. Code references tables by constant;
`contracts check` fails CI when generated files are stale. Frames must match a contract exactly
(names, order, dtypes); nothing is coerced. Money needs an explicit decimal precision. Forbidden columns
cannot be declared.

## Why constants, not names
String references hid uses from code search and broke silently on rename. That is the failure behind
the `usage.recon` dead-code misfire and the `final_vars` near-deletion.

## Consequences
Generators embed the schema path, so generation runs from the project root with relative paths
(otherwise `check` fails on other machines; a live-fire test caught this).
