"""DuckDB connections with DuckLake attached.

Extensions load from the pinned PyPI wheels by path. They are never downloaded from
extensions.duckdb.org, which may be blocked by an egress policy and is not version-locked.

One DuckDB instance per lake per process. A DuckDB-file catalog can be attached only once per process
(a second instance fails with "Unique file handle conflict"). So every ``Lake`` and ``Reader`` gets its
own cursor on a shared, reference-counted instance. The instance attaches the lake as ``lake``: read-write
for pipelines, read-only for agent-facing processes (``Lake(read_only=True)``). A reader attaches ``lake_at_<snapshot>_<n>`` read-only at ``SNAPSHOT_VERSION``, reusing the
metadata catalog, and ``USE``s it, so ``schema.table`` in reader SQL means "as of my snapshot".

Every cursor runs in UTC, so a box in Africa/Nairobi and CI in UTC compute the same partition dates.
"""

from __future__ import annotations

import functools
import importlib
import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from dataplat.config import LakeConfig
from dataplat.errors import LakeError

ALIAS = "lake"
META_CATALOG = f"__ducklake_metadata_{ALIAS}"
_EXT_MODULES = {
    "ducklake": "duckdb_extension_ducklake",
    "postgres_scanner": "duckdb_extension_postgres_scanner",
    "httpfs": "duckdb_extension_httpfs",
}


@functools.cache
def extension_path(name: str) -> str:
    mod = importlib.import_module(_EXT_MODULES[name])
    root = Path(mod.__file__ or "").parent
    found = next(
        root.rglob("*.duckdb_extension"), None
    )  # dataplat: allow[DPA003] -- locating a file inside an installed wheel
    if found is None:
        raise RuntimeError(f"{_EXT_MODULES[name]} is installed but ships no .duckdb_extension file")
    return str(found)


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


@dataclass
class _Instance:
    root: duckdb.DuckDBPyConnection
    read_only: bool
    refs: int = 0
    pinned: set[int] = field(default_factory=set)


_LOCK = threading.Lock()
_INSTANCES: dict[tuple[str, str, str], _Instance] = {}


def _key(cfg: LakeConfig) -> tuple[str, str, str]:
    return (cfg.attach_uri(), cfg.resolved_data_path(), cfg.metadata_schema() or "")


def _options(cfg: LakeConfig, *, snapshot: int | None = None, read_only: bool = False) -> str:
    opts = [f"DATA_PATH {_q(cfg.resolved_data_path())}"]
    if (schema := cfg.metadata_schema()) is not None:
        opts.append(f"METADATA_SCHEMA {_q(schema)}")
    if read_only and snapshot is None:
        opts.append("READ_ONLY")
    if snapshot is not None:
        # SNAPSHOT_VERSION makes the attach read-only. An explicit READ_ONLY would clash with the shared
        # metadata catalog, which is attached read-write.
        opts += [f"METADATA_CATALOG {_q(META_CATALOG)}", f"SNAPSHOT_VERSION {int(snapshot)}"]
    return ", ".join(opts)


def _open_instance(cfg: LakeConfig, read_only: bool) -> duckdb.DuckDBPyConnection:
    catalog_file = None if cfg.is_postgres else Path(cfg.attach_uri().removeprefix("ducklake:"))
    if read_only and catalog_file is not None and not catalog_file.exists():
        raise LakeError(
            f"no lake in namespace {cfg.namespace!r} yet ({catalog_file} does not exist): nothing has been "
            "written there. Run a pipeline, or `dataplat ns --init` to create an empty one."
        )
    con = duckdb.connect(config={"autoinstall_known_extensions": "false", "autoload_known_extensions": "false"})
    con.execute(f"LOAD {_q(extension_path('ducklake'))}")
    if cfg.is_postgres:
        con.execute(f"LOAD {_q(extension_path('postgres_scanner'))}")
    if cfg.is_s3:
        con.execute(f"LOAD {_q(extension_path('httpfs'))}")
        s3 = cfg.s3
        assert s3 is not None
        con.execute(
            "CREATE OR REPLACE SECRET dataplat_s3 (TYPE s3, "
            f"KEY_ID {_q(s3.key_id)}, SECRET {_q(s3.secret)}, ENDPOINT {_q(s3.endpoint)}, "
            f"REGION {_q(s3.region)}, URL_STYLE {_q(s3.url_style)}, USE_SSL {str(s3.use_ssl).lower()})"
        )
    else:
        Path(cfg.resolved_data_path()).mkdir(parents=True, exist_ok=True)
    if catalog_file is not None:
        catalog_file.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"ATTACH {_q(cfg.attach_uri())} AS {ALIAS} ({_options(cfg, read_only=read_only)})")
    return con


def acquire(cfg: LakeConfig, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """A new cursor on this process's instance for ``cfg``.

    One instance per catalog per process; the first opener picks its mode. If the process has no writer,
    a read-only request gets a read-only attach, so the database itself refuses writes (the normal case
    for agent-facing processes). If a read-write instance already exists, a read-only request shares it,
    and read-only is enforced by the API: Writer refuses, readers are pinned, raw ``lake.`` SQL is rejected.
    """
    with _LOCK:
        key = _key(cfg)
        inst = _INSTANCES.get(key)
        if inst is None:
            inst = _INSTANCES[key] = _Instance(_open_instance(cfg, read_only), read_only)
        elif inst.read_only and not read_only:
            raise LakeError(
                "this process already opened the lake read-only; a writer needs a read-write instance. "
                "Open the writer before any read-only handle, or write from a separate process."
            )
        inst.refs += 1
        cur = inst.root.cursor()
    cur.execute("SET TimeZone = 'UTC'")
    return cur


def release(cfg: LakeConfig, cur: duckdb.DuckDBPyConnection) -> None:
    try:
        cur.close()
    finally:
        with _LOCK:
            key = _key(cfg)
            inst = _INSTANCES.get(key)
            if inst is not None:
                inst.refs -= 1
                if inst.refs <= 0:
                    inst.root.close()
                    del _INSTANCES[key]


def attach_pinned(cur: duckdb.DuckDBPyConnection, cfg: LakeConfig, snapshot: int) -> str:
    """Make ``lake_at_<snapshot>`` (read-only, pinned) the cursor's default catalog.

    Pinned aliases are cached per instance and never detached individually. Detaching one would also
    detach the shared metadata catalog under the read-write ``lake``. Snapshots advance a few times a
    day, so the cache stays small; it is released with the instance.
    """
    alias = f"{ALIAS}_at_{int(snapshot)}"
    with _LOCK:
        inst = _INSTANCES[_key(cfg)]
        if snapshot not in inst.pinned:
            inst.root.execute(f"ATTACH {_q(cfg.attach_uri())} AS {alias} ({_options(cfg, snapshot=snapshot)})")
            inst.pinned.add(snapshot)
    cur.execute(f"USE {alias}")
    return alias
