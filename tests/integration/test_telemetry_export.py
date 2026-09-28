"""Telemetry leaves the process scrubbed: live-fire a failing workflow against a fake OTLP receiver."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.proto")
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

APP = Path(__file__).parent / "procs" / "telemetry_app.py"
RAW = [b"254712345678", b"12345678", b"jane@example.com"]


class _Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def receiver() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    server.received = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _attrs(kvs: object) -> dict[str, object]:
    out: dict[str, object] = {}
    for kv in kvs:  # type: ignore[attr-defined]
        value = kv.value
        out[kv.key] = getattr(value, value.WhichOneof("value")) if value.WhichOneof("value") else None
    return out


def test_failed_workflows_reach_the_collector_scrubbed(receiver: ThreadingHTTPServer, tmp_path: Path) -> None:
    host, port = receiver.server_address[:2]
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "DATAPLAT_", "OPENOBSERVE_"))},
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{host}:{port}",
        "OPENOBSERVE_USER": "probe@example.org",
        "OPENOBSERVE_PASSWORD": "not-a-real-password",
        "DATAPLAT_NS": "probe",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    run = subprocess.run(
        [sys.executable, str(APP), f"sqlite:///{tmp_path / 'dbos.sqlite'}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stderr[-3000:]

    received = receiver.received  # type: ignore[attr-defined]
    traces = [(h, b) for p, h, b in received if p == "/v1/traces"]
    logs = [(h, b) for p, h, b in received if p == "/v1/logs"]
    assert traces and logs, [p for p, _, _ in received]

    # credentials and stream travel as headers; the raw values never travel at all
    expected_auth = "Basic " + base64.b64encode(b"probe@example.org:not-a-real-password").decode()
    for headers, body in traces + logs:
        assert headers["authorization"] == expected_auth
        assert headers["stream-name"] == "telemetry_probe"
        for raw in RAW:
            assert raw not in body, raw

    spans = []
    for _, body in traces:
        for rs in ExportTraceServiceRequest.FromString(body).resource_spans:
            resource = _attrs(rs.resource.attributes)
            for ss in rs.scope_spans:
                spans += [(resource, s) for s in ss.spans]
    resource, _ = spans[0]
    assert resource["service.name"] == "telemetry_probe-probe"
    assert resource["dataplat.namespace"] == "probe" and resource["dataplat.role"] == "cli"

    by_name: dict[str, list[object]] = {}
    for _, s in spans:
        by_name.setdefault(s.name, []).append(s)
    healthy = by_name["healthy"][0]
    assert healthy.status.code == healthy.status.STATUS_CODE_OK
    assert _attrs(healthy.attributes)["dataplat.error"] == ""
    assert _attrs(healthy.attributes)["dbos.operation.type"] == "workflow"

    failed = [s for s in by_name["payout"] if s.status.code == s.status.STATUS_CODE_ERROR]
    assert len(failed) == 2, "foreground and background failures both exported"
    for s in failed:
        assert "9{12}" in s.status.message and "<email>" in s.status.message
        signature = _attrs(s.attributes)["dataplat.error"]
        assert signature == "payout failed for 9{12} (national id 9{8}, contact <email>)"

    records = []
    for _, body in logs:
        for rl in ExportLogsServiceRequest.FromString(body).resource_logs:
            for sl in rl.scope_logs:
                records += list(sl.log_records)
    traces_logged = [_attrs(r.attributes).get("exception.stacktrace") for r in records]
    stacktraces = [t for t in traces_logged if isinstance(t, str)]
    assert stacktraces, "the background failure is logged with its stack trace"
    assert any("9{12}" in t and "ValueError" in t for t in stacktraces)
