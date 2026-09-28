from __future__ import annotations

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from dbos import SetWorkflowID

from dataplat.actions import action, action_queue, approve, audit, propose, status
from dataplat.checks import Evidence, Verdict
from dataplat.gate import Answer, Refusal, answer, fresh_within, min_rows, partitions_complete
from dataplat.lakecore import Lake
from dataplat.lakecore.write import Writer
from tests.conftest import EVENTS, events_frame

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


def _count(reader) -> int:  # type: ignore[no-untyped-def]
    return int(reader.sql("SELECT count(*) AS n FROM usage.events")["n"][0])


def _count_on(day: date):  # type: ignore[no-untyped-def]
    return lambda reader: int(reader.sql("SELECT count(*) AS n FROM usage.events WHERE event_date = ?", [day])["n"][0])


def test_gate_answers_with_provenance_or_refuses(lake: Lake, writer: Writer) -> None:
    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 40), run_id="a")
    writer.commit_partition(EVENTS, D3, events_frame(D3, 40), run_id="c")  # D2 missing
    with lake.reader() as r:
        partial = answer(
            "events 1-3 Sep",
            reader=r,
            compute=_count,
            preconditions=[partitions_complete(EVENTS, D1, D3)],
            datasets=[EVENTS],
        )
        assert isinstance(partial, Refusal) and not partial.ok
        assert partial.reasons[0].measured["missing"] == 1
        assert "blocked metric" in str(partial)
        full = answer(
            "events 1 Sep",
            reader=r,
            compute=_count_on(D1),
            preconditions=[partitions_complete(EVENTS, D1, D1), min_rows(EVENTS, D1, D1, 30)],
            datasets=[EVENTS],
        )
        assert isinstance(full, Answer) and full.value == 40
        assert full.provenance.snapshot == r.snapshot and full.provenance.datasets == ("usage.events",)
        thin = answer("events 1 Sep", reader=r, compute=_count, preconditions=[min_rows(EVENTS, D1, D1, 1000)])
        assert isinstance(thin, Refusal) and thin.reasons[0].verdict is Verdict.INCONCLUSIVE
        stale = answer(
            "latest", reader=r, compute=_count, preconditions=[fresh_within(EVENTS, 1, today=date(2026, 9, 10))]
        )
        assert isinstance(stale, Refusal) and stale.reasons[0].measured["lag_days"] == 7


# -- actions -----------------------------------------------------------------------------
SENT: list[str] = []


def artist_known(artist_id: str, channel: str) -> Evidence:
    ok = artist_id.startswith("art_")
    return Evidence("artist_known", Verdict.PASS if ok else Verdict.FAIL, {"known": ok})


@action("test.outreach", preconditions=[artist_known], approval_timeout_s=30)
def propose_outreach(artist_id: str, channel: str) -> dict[str, str]:
    SENT.append(artist_id)
    return {"sent_to": artist_id, "channel": channel}


WORLD = {"artist_still_open": True}


def still_open(artist_id: str) -> Evidence:
    return Evidence("still_open", Verdict.PASS if WORLD["artist_still_open"] else Verdict.FAIL)


@action("test.recheck", preconditions=[still_open], approval_timeout_s=30)
def contact(artist_id: str) -> str:
    SENT.append(f"contact:{artist_id}")
    return "sent"


@action("test.note", approval=False)
def add_note(artist_id: str) -> str:
    return f"noted {artist_id}"


def _wait_for(wid: str, want: str) -> None:
    for _ in range(100):
        if status(wid) == want:
            return
        time.sleep(0.05)
    raise AssertionError(f"{wid} never reached {want}")


@pytest.mark.usefixtures("dbos_runtime")
def test_action_lifecycle(dbos_runtime) -> None:  # type: ignore[no-untyped-def]
    SENT.clear()
    h = dbos_runtime.start_workflow(propose_outreach, "art_1", "email")
    _wait_for(h.get_workflow_id(), "pending_approval")
    assert SENT == []  # nothing happens before a human says yes
    approve(h.get_workflow_id(), by="nyasha")
    out = h.get_result()
    assert out["status"] == "done" and out["approver"] == "nyasha" and SENT == ["art_1"]

    with SetWorkflowID(h.get_workflow_id()):
        assert propose_outreach("art_1", "email")["status"] == "done"
    assert SENT == ["art_1"]  # replay does not resend

    rej = dbos_runtime.start_workflow(propose_outreach, "art_2", "sms")
    _wait_for(rej.get_workflow_id(), "pending_approval")
    approve(rej.get_workflow_id(), by="nyasha", decision="rejected", note="not yet")
    assert rej.get_result()["status"] == "rejected" and "art_2" not in SENT

    refused = propose_outreach("unknown", "email")
    assert refused["status"] == "refused" and refused["evidence"][0]["verdict"] == "fail"

    assert add_note("art_3")["value"] == "noted art_3"
    trail = audit("test.outreach")
    assert {t["status"] for t in trail} == {"SUCCESS"} and len(trail) == 3
    assert all(t["app_version"] == "test" for t in trail)


def test_census_frame_is_counts_only(lake: Lake, writer: Writer) -> None:
    from dataplat.trust import census

    writer.ensure_table(EVENTS)
    writer.commit_partition(EVENTS, D1, events_frame(D1, 3), run_id="a")
    with lake.reader() as r:
        c = census(r, [EVENTS], today=D2)
    assert c.columns[0] == "dataset" and c.schema["rows"] == pl.Int64
    assert not any("phone" in col or "user" in col for col in c.columns)


@pytest.mark.usefixtures("dbos_runtime")
def test_propose_enqueues_for_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAPLAT_NS", "test")
    SENT.clear()
    action_queue()  # a launched process listens on the namespaced queue (here: this test process)
    wid = propose(propose_outreach, "art_9", "email")
    _wait_for(wid, "pending_approval")
    approve(wid, by="owner")
    from dbos import DBOS

    assert DBOS.retrieve_workflow(wid).get_result()["status"] == "done"
    assert SENT == ["art_9"]


def test_worker_executes_what_a_cli_process_proposes(tmp_path: Path) -> None:
    """The deployment model: a worker process runs actions; short-lived CLI processes propose and
    approve them, and never execute or recover them."""
    app = Path(__file__).parent / "procs" / "actions_app.py"
    db, log = f"sqlite:///{tmp_path}/sys.sqlite", str(tmp_path / "effects.log")
    worker = subprocess.Popen([sys.executable, str(app), "worker", db, log, "25"])
    try:
        for _ in range(300):  # the worker creates the system database first, as in production
            if Path(log + ".ready").exists():
                break
            time.sleep(0.1)
        out = subprocess.run([sys.executable, str(app), "cli", db, log], capture_output=True, text=True, timeout=120)
        assert "STATUS pending_approval" in out.stdout, out.stderr[-2000:]
        assert "RESULT done" in out.stdout, out.stderr[-2000:]
        assert Path(log).read_text().splitlines() == ["effect:artist-1"]  # executed once, by the worker
    finally:
        worker.terminate()
        worker.wait(timeout=30)


@pytest.mark.usefixtures("dbos_runtime")
def test_preconditions_are_rechecked_after_approval(dbos_runtime) -> None:  # type: ignore[no-untyped-def]
    SENT.clear()
    WORLD["artist_still_open"] = True
    h = dbos_runtime.start_workflow(contact, "art_7")
    _wait_for(h.get_workflow_id(), "pending_approval")
    WORLD["artist_still_open"] = False  # the world changed while the request waited
    approve(h.get_workflow_id(), by="owner")
    assert h.get_result()["status"] == "refused_after_approval" and SENT == []
