"""The lake: DuckLake catalog (Postgres in production) + Parquet on RustFS.

Read side (import anywhere)::

    from dataplat.lakecore import connect

    with connect().reader() as r:  # pinned to one snapshot
        df = r.scan(tables.EVENTS_CLEAN, where="event_date = ?", params=[d])

Write side (``<pkg>.pipelines`` only, enforced by ``dataplat lint``)::

    from dataplat.lakecore.write import Writer

Publish side (fixed-key documents outside table storage, e.g. rendered JSON snapshots; same
``<pkg>.pipelines``-only rule)::

    from dataplat.lakecore.publish import PublishTarget, publish_bytes, publish_documents
"""

from dataplat.config import LakeConfig
from dataplat.lakecore.read import Lake, Reader, connect

__all__ = ["Lake", "LakeConfig", "Reader", "connect"]
