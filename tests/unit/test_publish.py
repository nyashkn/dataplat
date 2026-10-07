from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dataplat.errors import ConfigError, PublishExistsError
from dataplat.lakecore.publish import PublishTarget, publish_bytes, publish_documents


def test_target_needs_exactly_one_of_root_or_bucket() -> None:
    with pytest.raises(ConfigError):
        PublishTarget()
    with pytest.raises(ConfigError):
        PublishTarget(root=Path("/tmp/x"), bucket="b")


def test_target_needs_s3_config_with_bucket() -> None:
    with pytest.raises(ConfigError):
        PublishTarget(bucket="example-bronze")


def test_publish_bytes_writes_once(tmp_path: Path) -> None:
    target = PublishTarget(root=tmp_path, prefix="reports/rep_digest/v1")
    receipt = publish_bytes(target, "send=A/period=2026-09-27/all.json", b'{"a": 1}')
    assert receipt.key == "reports/rep_digest/v1/send=A/period=2026-09-27/all.json"
    assert receipt.sha256 == hashlib.sha256(b'{"a": 1}').hexdigest()
    assert receipt.bytes == len(b'{"a": 1}')
    on_disk = tmp_path / "reports/rep_digest/v1/send=A/period=2026-09-27/all.json"
    assert on_disk.read_bytes() == b'{"a": 1}'


def test_publish_bytes_refuses_existing_key(tmp_path: Path) -> None:
    target = PublishTarget(root=tmp_path)
    publish_bytes(target, "a.json", b"1")
    with pytest.raises(PublishExistsError, match="already exists"):
        publish_bytes(target, "a.json", b"2")
    # the original write is untouched by the refused attempt
    assert (tmp_path / "a.json").read_bytes() == b"1"


def test_publish_bytes_overwrite_replaces(tmp_path: Path) -> None:
    target = PublishTarget(root=tmp_path)
    publish_bytes(target, "a.json", b"1")
    publish_bytes(target, "a.json", b"2", overwrite=True)
    assert (tmp_path / "a.json").read_bytes() == b"2"


def test_publish_documents_writes_manifest_last_with_hashes_and_count(tmp_path: Path) -> None:
    target = PublishTarget(root=tmp_path, prefix="send=B/period=2026-09-27")
    docs = {"jim.json": b'{"rep": "jim"}', "mike.json": b'{"rep": "mike"}'}
    result = publish_documents(target, docs, extra={"schema_version": "1.0.0"})

    assert {r.key.rsplit("/", 1)[-1] for r in result.documents} == {"jim.json", "mike.json"}
    assert result.manifest is not None
    assert result.manifest.key == "send=B/period=2026-09-27/_manifest.json"

    manifest = json.loads((tmp_path / "send=B/period=2026-09-27/_manifest.json").read_bytes())
    assert manifest["count"] == 2
    assert manifest["schema_version"] == "1.0.0"
    by_key = {f["key"]: f for f in manifest["files"]}
    assert by_key["send=B/period=2026-09-27/jim.json"]["sha256"] == hashlib.sha256(docs["jim.json"]).hexdigest()
    assert by_key["send=B/period=2026-09-27/jim.json"]["bytes"] == len(docs["jim.json"])


def test_publish_documents_stops_before_manifest_if_a_document_conflicts(tmp_path: Path) -> None:
    """A document key that already exists is refused before the manifest is written: no manifest
    should ever name a run that didn't actually complete."""
    target = PublishTarget(root=tmp_path)
    (tmp_path / "jim.json").write_bytes(b"stale")
    with pytest.raises(PublishExistsError):
        publish_documents(target, {"jim.json": b"new", "mike.json": b"new"})
    assert not (tmp_path / "_manifest.json").exists()
    assert not (tmp_path / "mike.json").exists()  # sorted order: jim.json fails before mike.json is written


def test_publish_bytes_idempotent_accepts_identical_bytes_but_not_different(tmp_path: Path) -> None:
    target = PublishTarget(root=tmp_path)
    first = publish_bytes(target, "k.json", b"one")
    again = publish_bytes(target, "k.json", b"one", idempotent=True)
    assert again.sha256 == first.sha256 and again.key == first.key and again.bytes == first.bytes
    with pytest.raises(PublishExistsError):
        publish_bytes(target, "k.json", b"two", idempotent=True)
    with pytest.raises(PublishExistsError):  # idempotent is opt-in
        publish_bytes(target, "k.json", b"one")
