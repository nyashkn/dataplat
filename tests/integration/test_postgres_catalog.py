"""The production catalog path: DuckLake on Postgres, namespaces as metadata schemas, two processes."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from dataplat.config import LakeConfig
from dataplat.lakecore import Lake
from dataplat.lakecore.write import Writer
from tests.conftest import EVENTS, events_frame

pgserver = pytest.importorskip("pgserver")
pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def pg(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    data = tmp_path_factory.mktemp("pg")
    try:
        srv = pgserver.get_server(str(data), cleanup_mode="stop")
    except Exception as err:  # pragma: no cover - environment dependent
        pytest.skip(f"pgserver unavailable: {err}")
    srv.psql("CREATE DATABASE lakecat;")
    yield f"postgres:dbname=lakecat host={data} user=postgres"
    srv.cleanup()


def test_namespaces_are_isolated_on_postgres(pg: str, tmp_path: Path) -> None:
    main = LakeConfig(pg, f"{tmp_path}/data/", namespace="base", code_version="t")
    feat = main.for_namespace("feat_x")
    with Lake(main) as a, Lake(feat) as b:
        wa, wb = Writer(a), Writer(b)
        wa.ensure_table(EVENTS)
        wb.ensure_table(EVENTS)
        wa.commit_partition(EVENTS, date(2026, 9, 1), events_frame(date(2026, 9, 1), 2), run_id="main")
        wb.commit_partition(EVENTS, date(2026, 9, 1), events_frame(date(2026, 9, 1), 7), run_id="feat")
        with a.reader() as ra, b.reader() as rb:
            assert ra.scan(EVENTS).height == 2 and rb.scan(EVENTS).height == 7
        wa.ensure_table(EVENTS)  # drift check reads partitioning from the Postgres metadata schema
        wb.ensure_table(EVENTS)


def test_second_process_reads_a_consistent_snapshot(pg: str, tmp_path: Path) -> None:
    cfg = LakeConfig(pg, f"{tmp_path}/data/", namespace="proc", code_version="t")
    with Lake(cfg) as lake:
        w = Writer(lake)
        w.ensure_table(EVENTS)
        w.commit_partition(EVENTS, date(2026, 9, 1), events_frame(date(2026, 9, 1), 3), run_id="p")
    script = textwrap.dedent(
        f"""
        from dataplat.config import LakeConfig
        from dataplat.lakecore import Lake
        cfg = LakeConfig({pg!r}, {str(tmp_path) + "/data/"!r}, namespace="proc", code_version="t")
        with Lake(cfg, read_only=True) as lake, lake.reader() as r:
            print(r.sql("SELECT count(*) AS n FROM usage.events")["n"][0])
        """
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "3"
