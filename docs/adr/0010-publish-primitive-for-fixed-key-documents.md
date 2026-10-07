# 0010. A publish primitive for fixed-key documents, separate from the table writer

- Status: accepted

## Context
`dataplat.lakecore.write.Writer` commits DuckLake table partitions; it has no way to put a document
at a caller-chosen object key (a rendered JSON payload, a per-run `_manifest.json` index). Every
non-table output that needed a fixed path was going to end up as a hand-rolled `boto3`/`s3fs` call
outside `dataplat.lakecore` — the exact drift ADR 0001 closed off for table writes.

## Decision
Add `dataplat.lakecore.publish`: `publish_bytes(target, key, data, overwrite=False)` writes once —
refuses an existing key unless `overwrite=True` — and `publish_documents(target, {key: bytes, ...})`
writes every document, then a `_manifest.json` **last** (sha256, byte size, count per file), so a
manifest can never reference an object that failed to land. `PublishTarget` is either a local
filesystem root (tests/dev) or an S3-compatible bucket+prefix (RustFS, R2, ...) — same two call sites,
same write-once contract.

Built on [obstore](https://developmentseed.org/obstore/) (Rust-backed, small wheel — no
`boto3`/`s3fs`/`botocore`, all forbidden project-wide by the `storage-sdks` import contract) rather
than raw `boto3`: it speaks every S3-compatible backend through one API, including a `LocalStore` with
identical semantics for tests, and — the reason write-once is safe here at all — a real conditional
create (`mode="create"`, HTTP `If-None-Match: *`), not a check-then-put race.

## Evidence (probed live against RustFS `example-bronze`, 2026-09-28)
- `obs.put(store, key, data, mode="create")` on a fresh key succeeds.
- The same call repeated against the same key returns HTTP 412 Precondition Failed, surfaced by
  obstore as `AlreadyExistsError` and translated here to `PublishExistsError` — RustFS honors
  `If-None-Match`, so the conditional create is atomic, not a race window.
- `LocalStore` gives the same `AlreadyExistsError`/overwrite behavior against a temp directory
  (`tests/unit/test_publish.py`), so callers can be tested without any S3-compatible service.

## Consequences
`dataplat lint`'s `storage-sdks` contract (forbids `boto3`/`s3fs`/`botocore`/`minio` project-wide) now
also keeps every project on this one publish path for fixed-key documents, the same way DPA004 keeps
every project on `Writer` for table partitions. `publish_documents`'s manifest-last ordering is the
project-level contract for "did this run's outputs actually land" — a manifest present means every
file it names was written; a manifest absent means the run was interrupted before any of them could be
trusted.
