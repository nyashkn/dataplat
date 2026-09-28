"""The derived graph: rebuilt from the lake, checked against the lake, then published.

The graph is never a source of truth. Each build:

1. exports node and relation tables from a pinned lake snapshot (SQL in ``GraphSpec``),
2. loads them into a native LadybugDB file with ``COPY FROM``,
3. asks each canary question twice, as SQL on the lake and as Cypher on the graph; the row
   multisets must match,
4. only then atomically moves the file into place and updates the ``current`` pointer.

A failed parity check raises ``ParityError`` and leaves the published graph unchanged.

Why native files rather than Icebug-disk (see ADR 0005): with real_ladybug 0.15.3 and icebug-format
1.1.0, Icebug-backed tables answered forward single hops correctly, but returned ``[]`` silently for
backward (``<-``) and two-hop patterns and crashed on variable-length paths. The same queries were
correct on native tables. A wrong answer that looks like an empty result is the worst failure for an
agent, so the parity gate stays even on native tables.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataplat.checks.verdict import Evidence, Verdict
from dataplat.errors import ParityError
from dataplat.lakecore._connect import _q
from dataplat.lakecore.read import Reader


@dataclass(frozen=True)
class NodeSpec:
    name: str
    sql: str  # must return exactly the declared columns
    key: str
    columns: tuple[tuple[str, str], ...]  # (name, Ladybug type: INT64, STRING, DOUBLE, DATE, BOOLEAN, TIMESTAMP)


@dataclass(frozen=True)
class RelSpec:
    name: str
    src: str
    dst: str
    sql: str  # must return "from", "to" (node keys) then the declared property columns
    columns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Canary:
    """The same question in both languages. Rows are compared as multisets of tuples."""

    name: str
    sql: str
    cypher: str


@dataclass(frozen=True)
class GraphSpec:
    name: str
    nodes: tuple[NodeSpec, ...]
    rels: tuple[RelSpec, ...]
    canaries: tuple[Canary, ...] = field(default=())

    def __post_init__(self) -> None:
        names = {n.name for n in self.nodes}
        for r in self.rels:
            if r.src not in names or r.dst not in names:
                raise ValueError(f"rel {r.name}: {r.src}->{r.dst} references an undeclared node table")
        if not self.canaries:
            raise ValueError(f"graph {self.name} declares no canaries; parity cannot be checked")


def _lb() -> Any:
    try:
        import real_ladybug
    except ImportError as err:  # pragma: no cover
        raise ImportError("install the graph extra: dataplat[graph]") from err
    return real_ladybug


def _rows(result: Any) -> list[tuple[Any, ...]]:
    out = []
    while result.has_next():
        out.append(tuple(result.get_next()))
    return out


def build(spec: GraphSpec, reader: Reader, out_path: Path) -> dict[str, int]:
    """Build a native Ladybug database file at ``out_path`` from the reader's snapshot."""
    lb = _lb()
    if out_path.exists():
        raise FileExistsError(out_path)
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="dataplat-graph-") as staging:
        db = lb.Database(str(out_path))
        conn = lb.Connection(db)
        for n in spec.nodes:
            cols = ", ".join(f"{c} {t}" for c, t in n.columns)
            conn.execute(f"CREATE NODE TABLE {n.name}({cols}, PRIMARY KEY({n.key}))")
            counts[n.name] = _stage_and_copy(reader, conn, staging, n.name, n.sql)
        for r in spec.rels:
            props = "".join(f", {c} {t}" for c, t in r.columns)
            conn.execute(f"CREATE REL TABLE {r.name}(FROM {r.src} TO {r.dst}{props})")
            counts[r.name] = _stage_and_copy(reader, conn, staging, r.name, r.sql)
        conn.close()
        db.close()
    return counts


def _stage_and_copy(reader: Reader, conn: Any, staging: str, name: str, sql: str) -> int:
    path = os.path.join(staging, f"{name}.parquet")
    # Staging only: a temp file that feeds the graph loader and is deleted after. Not a lake write.
    reader.con.execute(
        f"COPY ({sql}) TO {_q(path)} (FORMAT parquet)"
    )  # dataplat: allow[DPA002] -- temp staging for the graph loader
    conn.execute(f"COPY {name} FROM {_q(path)}")
    return int(reader.con.execute(f"SELECT count(*) FROM read_parquet({_q(path)})").fetchone()[0])  # type: ignore[index]


def _norm(rows: list[tuple[Any, ...]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple("<null>" if v is None else str(v) for v in r) for r in rows)


def parity(spec: GraphSpec, reader: Reader, graph_path: Path) -> tuple[Evidence, ...]:
    lb = _lb()
    db = lb.Database(str(graph_path), read_only=True)
    conn = lb.Connection(db)
    out = []
    try:
        for c in spec.canaries:
            lake_rows = [tuple(r) for r in reader.con.execute(c.sql).fetchall()]
            graph_rows = _rows(conn.execute(c.cypher))
            a, b = _norm(lake_rows), _norm(graph_rows)
            same = a == b
            out.append(
                Evidence(
                    f"parity[{spec.name}.{c.name}]",
                    Verdict.PASS if same else Verdict.FAIL,
                    {
                        "lake_rows": sum(a.values()),
                        "graph_rows": sum(b.values()),
                        "only_in_lake": sum((a - b).values()),
                        "only_in_graph": sum((b - a).values()),
                    },
                )
            )
    finally:
        conn.close()
        db.close()
    return tuple(out)


def publish(spec: GraphSpec, reader: Reader, graph_dir: Path) -> dict[str, Any]:
    """Build, check parity, then atomically publish ``<graph_dir>/<name>/<snapshot>.lbug``."""
    target_dir = graph_dir / spec.name
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{reader.snapshot}.lbug"
    tmp = target_dir / f".{reader.snapshot}.{os.getpid()}.building.lbug"
    if tmp.exists():
        tmp.unlink()
    counts = build(spec, reader, tmp)
    evidence = parity(spec, reader, tmp)
    failing = [e for e in evidence if e.verdict is not Verdict.PASS]
    if failing:
        _remove(tmp)
        raise ParityError(f"graph {spec.name} not published: " + "; ".join(str(e) for e in failing))
    os.replace(tmp, final)
    meta = {
        "graph": spec.name,
        "snapshot": reader.snapshot,
        "path": final.name,
        "counts": counts,
        "code_version": reader.cfg.code_version,
        "built_at": datetime.now(UTC).isoformat(),
        "parity": [e.to_dict() for e in evidence],
    }
    pointer_tmp = target_dir / f".current.{os.getpid()}.json"
    pointer_tmp.write_text(json.dumps(meta, indent=2, default=str))
    os.replace(pointer_tmp, target_dir / "current.json")
    return meta


def open_current(graph_dir: Path, name: str) -> tuple[Any, dict[str, Any]]:
    """Read-only connection to the published graph, plus its metadata (snapshot = provenance)."""
    lb = _lb()
    meta = json.loads((graph_dir / name / "current.json").read_text())
    db = lb.Database(str(graph_dir / name / meta["path"]), read_only=True)
    return lb.Connection(db), meta


def query(conn: Any, cypher: str) -> list[tuple[Any, ...]]:
    return _rows(conn.execute(cypher))


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)
    for side in p.parent.glob(
        p.name + ".*"
    ):  # dataplat: allow[DPA003] -- ladybug WAL/shadow files next to our own temp file
        side.unlink(missing_ok=True)
