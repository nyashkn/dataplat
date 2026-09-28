"""A small OpenObserve API client for applying alerts as code.

It reuses the telemetry settings, so whatever can send telemetry can manage its alerts:

- the API root and organization come from ``OPENOBSERVE_URL`` + ``OPENOBSERVE_ORG``, or else from
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` (``http://host:5080/api/<org>``);
- credentials from ``OTEL_EXPORTER_OTLP_HEADERS`` (``Authorization=...``) or ``OPENOBSERVE_USER`` +
  ``OPENOBSERVE_PASSWORD``.

``apply`` is an upsert by alert name: alerts it doesn't know are left alone and reported, never deleted.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from dataplat.errors import ConfigError, DataplatError
from dataplat.observe.alerts import Alert
from dataplat.observe.telemetry import credentials

DEFAULT_FOLDER = "default"


class OpenObserveError(DataplatError):
    """The OpenObserve API refused a request."""


@dataclass(frozen=True)
class OpenObserve:
    base_url: str  # e.g. http://localhost:5080 (no /api)
    org: str
    headers: Mapping[str, str] = field(repr=False)
    timeout_s: float = 20.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OpenObserve:
        env = os.environ if env is None else env
        base, org = env.get("OPENOBSERVE_URL", "").rstrip("/"), env.get("OPENOBSERVE_ORG", "")
        endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
        if (not base or not org) and endpoint:
            parts = urlsplit(endpoint)
            m = re.match(r"^(.*)/api/([^/]+)$", parts.path)
            if m:
                base = base or f"{parts.scheme}://{parts.netloc}{m.group(1)}"
                org = org or m.group(2)
        if not base or not org:
            raise ConfigError(
                "OpenObserve is not configured: set OTEL_EXPORTER_OTLP_ENDPOINT=http://host:5080/api/<org> "
                "(or OPENOBSERVE_URL and OPENOBSERVE_ORG)"
            )
        auth = credentials(env)
        if not auth:
            raise ConfigError(
                "no OpenObserve credentials: set OPENOBSERVE_USER and OPENOBSERVE_PASSWORD "
                "(or Authorization in OTEL_EXPORTER_OTLP_HEADERS)"
            )
        return cls(base_url=base, org=org, headers=auth)

    def _request(self, method: str, path: str, body: Any = None, query: Mapping[str, str] | None = None) -> Any:
        url = f"{self.base_url}/api{path}" + (f"?{urlencode(query)}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {**self.headers, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise OpenObserveError(f"{method} {path} -> {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise OpenObserveError(f"{method} {path}: cannot reach {self.base_url} ({e.reason})") from e
        return json.loads(raw) if raw.strip() else None

    def destination_exists(self, name: str) -> bool:
        try:
            self._request("GET", f"/{self.org}/alerts/destinations/{quote(name)}")
        except OpenObserveError as e:
            if "-> 404" in str(e):
                return False
            raise
        return True

    def alerts_by_name(self, stream_type: str, stream: str) -> dict[str, str]:
        body = self._request("GET", f"/v2/{self.org}/alerts", query={"stream_type": stream_type, "stream_name": stream})
        return {item["name"]: str(item["alert_id"]) for item in (body or {}).get("list", [])}

    def create_alert(self, payload: Mapping[str, Any], folder: str = DEFAULT_FOLDER) -> None:
        self._request("POST", f"/v2/{self.org}/alerts", body=payload, query={"folder": folder})

    def update_alert(self, alert_id: str, payload: Mapping[str, Any]) -> None:
        self._request("PUT", f"/v2/{self.org}/alerts/{quote(alert_id)}", body=payload)


@dataclass(frozen=True)
class Plan:
    create: list[str]
    update: list[str]
    unmanaged: list[str]  # alerts on the same stream that these definitions don't cover

    def lines(self) -> list[str]:
        return (
            [f"create  {n}" for n in self.create]
            + [f"update  {n}" for n in self.update]
            + [f"leave   {n} (not defined here)" for n in self.unmanaged]
        )


def apply(client: OpenObserve, alerts: Sequence[Alert], destination: str, *, dry_run: bool = True) -> Plan:
    """Create or update ``alerts`` by name, all notifying ``destination`` (which must already exist)."""
    if not client.destination_exists(destination):
        raise OpenObserveError(
            f"alert destination {destination!r} does not exist in org {client.org!r}. Create it once in "
            "OpenObserve (Management > Alert destinations: email, Slack, Telegram or any webhook), then rerun."
        )
    existing: dict[str, str] = {}
    for stream_type, stream in sorted({(a.stream_type, a.stream) for a in alerts}):
        existing |= client.alerts_by_name(stream_type, stream)
    wanted = {a.name for a in alerts}
    plan = Plan(
        create=[a.name for a in alerts if a.name not in existing],
        update=[a.name for a in alerts if a.name in existing],
        unmanaged=sorted(n for n in existing if n not in wanted),
    )
    if not dry_run:
        for a in alerts:
            if a.name in existing:
                client.update_alert(existing[a.name], a.payload([destination]))
            else:
                client.create_alert(a.payload([destination]))
    return plan
