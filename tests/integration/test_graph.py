from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from dataplat.contracts import Column, TableContract
from dataplat.errors import ParityError
from dataplat.lakecore import Lake
from dataplat.lakecore.write import Writer

pytest.importorskip("real_ladybug")
pytestmark = pytest.mark.graph

from dataplat.graph import Canary, GraphSpec, NodeSpec, RelSpec, open_current, publish, query  # noqa: E402

ARTISTS = TableContract(
    "Artists",
    "catalog.artists",
    (Column("artist_id", "int64", required=True), Column("name", "string")),
    grain=("artist_id",),
)
SONGS = TableContract(
    "Songs", "catalog.songs", (Column("song_id", "int64", required=True), Column("title", "string")), grain=("song_id",)
)
PERF = TableContract(
    "Performed",
    "catalog.performed",
    (Column("artist_id", "int64", required=True), Column("song_id", "int64", required=True)),
)

CO_PERFORMERS = Canary(
    "co_performers",
    sql="SELECT a.artist_id, b.artist_id FROM catalog.performed a JOIN catalog.performed b "
    "ON a.song_id = b.song_id AND a.artist_id < b.artist_id",
    cypher="MATCH (a:artist)-[:performed]->(:song)<-[:performed]-(b:artist) WHERE a.artist_id < b.artist_id "
    "RETURN a.artist_id, b.artist_id",
)
SPEC = GraphSpec(
    name="catalog",
    nodes=(
        NodeSpec(
            "artist",
            "SELECT artist_id, name FROM catalog.artists",
            "artist_id",
            (("artist_id", "INT64"), ("name", "STRING")),
        ),
        NodeSpec(
            "song", "SELECT song_id, title FROM catalog.songs", "song_id", (("song_id", "INT64"), ("title", "STRING"))
        ),
    ),
    rels=(
        RelSpec("performed", "artist", "song", 'SELECT artist_id AS "from", song_id AS "to" FROM catalog.performed'),
    ),
    canaries=(
        CO_PERFORMERS,
        Canary(
            "songs_per_artist",
            sql="SELECT artist_id, count(*) FROM catalog.performed GROUP BY 1",
            cypher="MATCH (a:artist)-[:performed]->(s:song) RETURN a.artist_id, count(s)",
        ),
    ),
)


@pytest.fixture
def seeded(lake: Lake, writer: Writer) -> Lake:
    for c in (ARTISTS, SONGS, PERF):
        writer.ensure_table(c)
    writer.replace(ARTISTS, pl.DataFrame({"artist_id": [1, 2, 3], "name": ["A", "B", "C"]}), run_id="a")
    writer.replace(
        SONGS, pl.DataFrame({"song_id": [10, 11, 12, 13], "title": ["s10", "s11", "s12", "s13"]}), run_id="s"
    )
    writer.replace(PERF, pl.DataFrame({"artist_id": [1, 1, 2, 2, 3], "song_id": [10, 11, 11, 12, 13]}), run_id="p")
    return lake


def test_publish_checks_parity_then_swaps_pointer(seeded: Lake, tmp_path: Path) -> None:
    with seeded.reader() as r:
        meta = publish(SPEC, r, tmp_path / "graphs")
    assert meta["counts"] == {"artist": 3, "song": 4, "performed": 5}
    assert all(p["verdict"] == "pass" for p in meta["parity"])
    conn, current = open_current(tmp_path / "graphs", "catalog")
    assert current["snapshot"] == meta["snapshot"]
    assert query(
        conn, "MATCH (s:song)<-[:performed]-(a:artist) WHERE s.song_id = 11 RETURN a.artist_id ORDER BY a.artist_id"
    ) == [(1,), (2,)]
    with pytest.raises(RuntimeError, match="read-only"):
        conn.execute("CREATE (:artist {artist_id: 9, name: 'x'})")


def test_parity_failure_publishes_nothing(seeded: Lake, tmp_path: Path) -> None:
    with seeded.reader() as r:
        publish(SPEC, r, tmp_path / "graphs")
    before = json.loads((tmp_path / "graphs" / "catalog" / "current.json").read_text())
    lying = GraphSpec(
        name="catalog",
        nodes=SPEC.nodes,
        rels=SPEC.rels,
        canaries=(Canary("wrong_on_purpose", sql="SELECT 1", cypher="MATCH (a:artist) RETURN count(a)"),),
    )
    with seeded.reader() as r, pytest.raises(ParityError, match="not published"):
        publish(lying, r, tmp_path / "graphs")
    after = json.loads((tmp_path / "graphs" / "catalog" / "current.json").read_text())
    assert after == before
    assert not list((tmp_path / "graphs" / "catalog").glob(".*building*"))


def test_spec_requires_canaries() -> None:
    with pytest.raises(ValueError, match="canaries"):
        GraphSpec(name="x", nodes=SPEC.nodes, rels=SPEC.rels, canaries=())
