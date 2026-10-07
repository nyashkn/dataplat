"""Applying alerts: an idempotent upsert by name against a fake OpenObserve API."""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from dataplat.errors import ConfigError
from dataplat.observe.alerts import standard_alerts
from dataplat.observe.openobserve import OpenObserve, OpenObserveError, apply

AUTH = "Basic " + base64.b64encode(b"ops@example.org:pw").decode()


class _FakeO2(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: Any = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method: str) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        state["auth"].add(self.headers.get("Authorization"))
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        parts = url.path.strip("/").split("/")
        if parts[:4] == ["api", "default", "alerts", "destinations"] and method == "GET":
            return self._reply(200, {"name": parts[4]}) if parts[4] == "ops" else self._reply(404, {"message": "no"})
        if parts[:4] == ["api", "v2", "default", "alerts"]:
            if method == "GET":
                items = [
                    {"alert_id": i, "name": a["name"]}
                    for i, a in state["alerts"].items()
                    if a["stream_type"] == query["stream_type"] and a["stream_name"] == query["stream_name"]
                ]
                return self._reply(200, {"list": items})
            if method == "POST":
                assert query.get("folder") == "default"
                state["alerts"][f"id{len(state['alerts'])}"] = body
                state["writes"].append(("POST", body["name"]))
                return self._reply(200, {"code": 200})
            if method == "PUT":
                state["alerts"][parts[4]] = body
                state["writes"].append(("PUT", body["name"]))
                return self._reply(200, {"code": 200})
        return self._reply(404, {"message": "unknown route"})

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def o2() -> Iterator[tuple[OpenObserve, dict[str, Any]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeO2)
    unmanaged = {"name": "hand_made", "stream_type": "traces", "stream_name": "example"}
    server.state = {"alerts": {"keep": unmanaged}, "writes": [], "auth": set()}  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    client = OpenObserve.from_env(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{host}:{port}/api/default",
            "OPENOBSERVE_USER": "ops@example.org",
            "OPENOBSERVE_PASSWORD": "pw",
        }
    )
    yield client, server.state  # type: ignore[attr-defined]
    server.shutdown()


def test_apply_is_an_upsert_by_name(o2: tuple[OpenObserve, dict[str, Any]]) -> None:
    client, state = o2
    alerts = standard_alerts("example")
    plan = apply(client, alerts, "ops", dry_run=True)
    assert plan.create == ["example_workflow_failures", "example_new_errors"] and plan.update == []
    assert plan.unmanaged == ["hand_made"] and state["writes"] == []  # a dry run writes nothing

    apply(client, alerts, "ops", dry_run=False)
    assert state["writes"] == [("POST", "example_workflow_failures"), ("POST", "example_new_errors")]
    again = apply(client, alerts, "ops", dry_run=False)
    assert again.create == [] and again.update == ["example_workflow_failures", "example_new_errors"]
    assert state["writes"][-2:] == [("PUT", "example_workflow_failures"), ("PUT", "example_new_errors")]
    assert state["alerts"]["keep"]["name"] == "hand_made"  # never deleted
    assert state["auth"] == {AUTH}
    assert "pw" not in repr(client)


def test_apply_needs_an_existing_destination(o2: tuple[OpenObserve, dict[str, Any]]) -> None:
    client, _ = o2
    with pytest.raises(OpenObserveError, match="destination 'pager' does not exist"):
        apply(client, standard_alerts("example"), "pager")


def test_client_configuration_errors_are_explicit() -> None:
    with pytest.raises(ConfigError, match="not configured"):
        OpenObserve.from_env({})
    with pytest.raises(ConfigError, match="no OpenObserve credentials"):
        OpenObserve.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://o2:5080/api/default"})
    c = OpenObserve.from_env(
        {
            "OPENOBSERVE_URL": "http://o2:5080/",
            "OPENOBSERVE_ORG": "acme",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20x",
        }
    )
    assert (c.base_url, c.org, dict(c.headers)) == ("http://o2:5080", "acme", {"Authorization": "Basic x"})
