"""Publish side of the lake: a document at a fixed object path, written once.

Not the DuckLake catalog (``dataplat.lakecore.write.Writer``, table partitions, ``_meta.dataset_runs``):
this is for artifacts that live at a caller-chosen key outside table storage — a rendered payload JSON,
a per-run ``_manifest.json`` index — the shape most non-table pipeline outputs actually need.

Backed by `obstore <https://developmentseed.org/obstore/>`_: a small Rust-backed object-store client
(no boto3/s3fs — ``dataplat lint`` DPA-forbidden anywhere outside this module) that talks every
S3-compatible endpoint through the same API, including RustFS at ``example-bronze`` (proven live,
see ``publish_bytes`` docstring) and Cloudflare R2, plus a local-filesystem backend for tests and dev
with the identical write-once semantics — no moto/minio dependency needed. Path-style URLs
(``virtual_hosted_style_request=False``) work with any bucket-in-path S3-compatible host, RustFS
included.

Write-once: ``publish_bytes`` refuses an existing key unless ``overwrite=True``, using obstore's native
conditional create (``mode="create"``, an HTTP ``If-None-Match: *`` precondition) rather than a
check-then-put race. Confirmed live against RustFS 2026-09-28: a second ``mode="create"`` put against a
key already written gets a 412 Precondition Failed, translated here to ``PublishExistsError``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import obstore as obs
from obstore import exceptions as obs_exceptions
from obstore.store import LocalStore, ObjectStore, S3Store

from dataplat.config import S3Config
from dataplat.errors import ConfigError, PublishExistsError

__all__ = ["ManifestReceipt", "PublishReceipt", "PublishTarget", "publish_bytes", "publish_documents"]


@dataclass(frozen=True)
class PublishTarget:
    """Where documents are published, plus the key prefix every document is written under.

    Exactly one of ``root`` (local filesystem, tests/dev) or ``bucket``+``s3`` (S3-compatible object
    storage) must be set. ``prefix`` is joined onto every key given to :func:`publish_bytes`.
    """

    prefix: str = ""
    root: Path | None = None
    bucket: str | None = None
    s3: S3Config | None = None

    def __post_init__(self) -> None:
        if (self.root is None) == (self.bucket is None):
            raise ConfigError("PublishTarget needs exactly one of root= (local) or bucket= (S3)")
        if self.bucket is not None and self.s3 is None:
            raise ConfigError("PublishTarget(bucket=...) needs s3=")

    def full_key(self, key: str) -> str:
        prefix = self.prefix.strip("/")
        key = key.strip("/")
        return f"{prefix}/{key}" if prefix else key


@dataclass(frozen=True)
class PublishReceipt:
    key: str
    sha256: str
    bytes: int
    content_type: str | None = None


@dataclass(frozen=True)
class ManifestReceipt:
    documents: list[PublishReceipt] = field(default_factory=list)
    manifest: PublishReceipt | None = None


def _store(target: PublishTarget) -> ObjectStore:
    if target.root is not None:
        return LocalStore(prefix=str(target.root), mkdir=True)
    cfg = target.s3
    assert cfg is not None
    endpoint = (
        cfg.endpoint if urlsplit(cfg.endpoint).scheme else f"{'https' if cfg.use_ssl else 'http'}://{cfg.endpoint}"
    )
    return S3Store(
        target.bucket,
        endpoint=endpoint,
        access_key_id=cfg.key_id,
        secret_access_key=cfg.secret,
        region=cfg.region,
        virtual_hosted_style_request=cfg.url_style != "path",
        client_options={"allow_http": not cfg.use_ssl},
    )


def publish_bytes(
    target: PublishTarget,
    key: str,
    data: bytes,
    *,
    content_type: str | None = None,
    overwrite: bool = False,
    idempotent: bool = False,
) -> PublishReceipt:
    """Write ``data`` at ``target``'s prefix + ``key``, once.

    Refuses (``PublishExistsError``) if the object already exists, unless ``overwrite=True``. With
    ``idempotent=True`` an existing object holding exactly these bytes counts as done and returns the same
    receipt (a retry or a replayed workflow step re-writing its own output); different bytes still refuse. The
    refusal is a real conditional-write precondition on S3-compatible targets (an atomic
    ``If-None-Match: *``, no read-then-write race window) and an ``os.O_EXCL``-equivalent exclusive
    create on the local backend.
    """
    store = _store(target)
    full_key = target.full_key(key)
    # LocalStore (tests/dev) does not implement `put_opts` with attributes; content_type is still
    # recorded on the returned PublishReceipt either way, just not sent as a backend header locally.
    attributes = {"Content-Type": content_type} if content_type and not isinstance(store, LocalStore) else None
    try:
        obs.put(store, full_key, data, mode="overwrite" if overwrite else "create", attributes=attributes)
    except obs_exceptions.AlreadyExistsError as e:
        if idempotent and bytes(obs.get(store, full_key).bytes()) == data:
            return PublishReceipt(
                key=full_key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), content_type=content_type
            )
        raise PublishExistsError(
            f"{full_key} already exists at this publish target; pass overwrite=True to replace it"
        ) from e
    return PublishReceipt(
        key=full_key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), content_type=content_type
    )


def publish_documents(
    target: PublishTarget,
    documents: Mapping[str, bytes],
    *,
    content_type: str = "application/json",
    overwrite: bool = False,
    manifest_key: str = "_manifest.json",
    extra: Mapping[str, Any] | None = None,
) -> ManifestReceipt:
    """Publish every document, then ``manifest_key`` LAST: a manifest never references an unwritten file.

    The manifest holds each document's key, sha256 and byte size, a count, and ``extra`` (schema
    version, as-of, generated-at — whatever the caller's contract requires); its own key/sha256/size
    are returned as ``ManifestReceipt.manifest``, not included inside its own body.
    """
    receipts = [
        publish_bytes(target, key, documents[key], content_type=content_type, overwrite=overwrite)
        for key in sorted(documents)
    ]
    manifest = {
        "count": len(receipts),
        "files": [{"key": r.key, "sha256": r.sha256, "bytes": r.bytes} for r in sorted(receipts, key=lambda r: r.key)],
        **(dict(extra) if extra else {}),
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode()
    manifest_receipt = publish_bytes(
        target, manifest_key, manifest_bytes, content_type="application/json", overwrite=overwrite
    )
    return ManifestReceipt(documents=receipts, manifest=manifest_receipt)
