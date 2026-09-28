# 0005. The graph is native LadybugDB files, rebuilt from the lake behind a parity gate

- Status: accepted (revisit Icebug when upstream fixes land)

## Context
The plan was Icebug (graph-aware Parquet, CSR) read by LadybugDB, so the graph could live next to the
lake without a load step.

## Evidence (real_ladybug 0.15.3, icebug-format 1.1.0, same data both ways)
| Query | Native tables | Icebug-disk |
|---|---|---|
| forward single hop `(a)-[:r]->(b)` | correct | correct |
| backward `(b)<-[:r]-(a)` | correct | `[]` silently |
| two-hop `(a)-[:r]->(x)-[:r2]->(b)` | 7 rows | `[]` silently |
| variable-length `-[:r*1..3]-` | correct | segfault |

Also: `--add-reverse-edges` refuses heterogeneous relations, the `--source-db` path writes
`storage=''`, and LadybugDB expects files named `<prefix>_<kind>_<name>.parquet`. The algo extension
download (extension.ladybugdb.com) was blocked by egress policy in the test environment.

## Decision
`dataplat.graph` builds a native LadybugDB file from a pinned lake snapshot (`COPY FROM` staged
Parquet), runs every canary question twice (SQL on the lake, Cypher on the graph), and publishes only
when every canary matches as a multiset. It then swaps a `current.json` pointer atomically and serves
the file read-only. Canary sets should include a backward traversal; the template's example does.

## Consequences
A rebuild costs one load per night. A wrong answer that looks like "no results" cannot reach an agent.
