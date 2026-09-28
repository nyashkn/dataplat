"""OpenTelemetry for dataplat processes: DBOS workflow and step spans plus logs, over OTLP/HTTP.

Off unless the environment turns it on, so tests, CI and laptops export nothing by default:

- ``OTEL_EXPORTER_OTLP_ENDPOINT``: base URL; ``/v1/traces`` and ``/v1/logs`` are appended (OpenObserve:
  ``http://host:5080/api/<org>``). ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` / ``..._LOGS_ENDPOINT`` override
  one signal with a full URL.
- ``OTEL_EXPORTER_OTLP_HEADERS`` (standard, e.g. ``Authorization=Basic%20...``), or ``OPENOBSERVE_USER`` +
  ``OPENOBSERVE_PASSWORD`` for Basic auth. Credentials come from the environment only: Infisical on the
  box, ``.env`` for a local OpenObserve.
- ``DATAPLAT_TELEMETRY_STREAM``: the OpenObserve stream, sent as the ``stream-name`` header. ``launch()``
  defaults it to the project's app name, so worktrees report into the project's stream under their own
  ``service.name``.
- ``DATAPLAT_TELEMETRY=off`` turns it off whatever else is set.

``launch()`` calls ``configure()`` before DBOS starts, so DBOS's tracer and its log handler use these
providers. Everything leaves through ``scrub``: span status messages, span and event attributes, log
bodies and log attributes (exception messages and stack traces included). Every exported span also
carries ``dataplat.error``: the error signature of a failed span, empty otherwise. Alerts group by it.
"""

from __future__ import annotations

import base64
import dataclasses
import logging
import os
import re
from collections.abc import Mapping, Sequence
from copy import copy
from typing import Any, cast
from urllib.parse import unquote

from dataplat.errors import ConfigError
from dataplat.observe.scrub import error_signature, scrub, scrub_stacktrace

OFF_VALUES = frozenset({"off", "0", "false", "no"})
ERROR_ATTRIBUTE = "dataplat.error"
# Correlation ids and code locations DBOS and the logging bridge attach. They identify runs and code,
# not people, so they leave as they are; every other string attribute is scrubbed.
PASS_THROUGH = frozenset(
    {
        "dbos.operation.workflow_id",
        "dbos.application.id",
        "dbos.application.version",
        "dbos.executor.id",
        "dbos.queue.name",
        "operationUUID",
        "applicationID",
        "applicationVersion",
        "executorID",
        "queueName",
        "traceId",
        "spanId",
        "code.file.path",
        "code.function.name",
        "code.line.number",
    }
)

_state: dict[str, Any] = {"providers": None}


@dataclasses.dataclass(frozen=True)
class TelemetrySettings:
    traces_endpoint: str | None
    logs_endpoint: str | None
    headers: Mapping[str, str] = dataclasses.field(repr=False)  # may hold credentials: never printed
    stream: str | None = None
    log_level: str = "WARNING"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, default_stream: str | None = None
    ) -> TelemetrySettings | None:
        env = os.environ if env is None else env
        if env.get("DATAPLAT_TELEMETRY", "").strip().lower() in OFF_VALUES:
            return None
        base = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
        traces = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip() or (f"{base}/v1/traces" if base else "")
        logs = env.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "").strip() or (f"{base}/v1/logs" if base else "")
        if not traces and not logs:
            return None
        headers = {**parse_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS", "")), **credentials(env)}
        stream = env.get("DATAPLAT_TELEMETRY_STREAM", "").strip() or default_stream
        if stream and "stream-name" not in {k.lower() for k in headers}:
            headers["stream-name"] = stream_name(stream)
        return cls(
            traces_endpoint=traces or None,
            logs_endpoint=logs or None,
            headers=headers,
            stream=stream,
            log_level=env.get("DATAPLAT_TELEMETRY_LOG_LEVEL", "WARNING").upper(),
        )


def stream_name(name: str) -> str:
    """A stream name as OpenObserve stores it: characters other than letters, digits, ``_`` and ``:``
    become ``_``, lowercased."""
    return re.sub(r"[^A-Za-z0-9_:]", "_", name).lower()


def credentials(env: Mapping[str, str]) -> dict[str, str]:
    """The Authorization header, from ``OTEL_EXPORTER_OTLP_HEADERS`` or ``OPENOBSERVE_USER``/``_PASSWORD``."""
    for key, value in parse_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS", "")).items():
        if key.lower() == "authorization":
            return {key: value}
    user, password = env.get("OPENOBSERVE_USER", ""), env.get("OPENOBSERVE_PASSWORD", "")
    if user and password:
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}
    return {}


def parse_headers(raw: str) -> dict[str, str]:
    """``k1=v1,k2=v2`` with URL-encoded values, as the OpenTelemetry spec defines the headers variable."""
    headers: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip():
            headers[key.strip()] = unquote(value.strip())
    return headers


def configured() -> bool:
    return _state["providers"] is not None


def configure(
    settings: TelemetrySettings,
    *,
    service_name: str,
    service_version: str = "dev",
    role: str = "cli",
    namespace: str = "main",
) -> bool:
    """Install scrubbing trace and log providers for this process. Once per process; later calls no-op."""
    if configured():
        return False
    try:
        from opentelemetry import _logs, trace
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.logging.handler import LoggingHandler
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:  # pragma: no cover - depends on the installed extras
        raise ConfigError(
            "telemetry is configured (OTEL_EXPORTER_OTLP_ENDPOINT) but OpenTelemetry is not installed: "
            "depend on dataplat[telemetry], or set DATAPLAT_TELEMETRY=off"
        ) from e

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "dataplat.role": role,
            "dataplat.namespace": namespace,
        }
    )
    headers = dict(settings.headers)
    tracer_provider = TracerProvider(resource=resource)
    if settings.traces_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.traces_endpoint, headers=headers)
        # the wrappers are duck-typed exporters, so this module imports without OpenTelemetry installed
        tracer_provider.add_span_processor(BatchSpanProcessor(cast(Any, ScrubbingSpanExporter(exporter))))
    trace.set_tracer_provider(tracer_provider)

    logger_provider = LoggerProvider(resource=resource)
    if settings.logs_endpoint:
        log_exporter = OTLPLogExporter(endpoint=settings.logs_endpoint, headers=headers)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(cast(Any, ScrubbingLogExporter(log_exporter))))
    _logs.set_logger_provider(logger_provider)
    # Warnings and errors from every logger (DBOS attaches its own handler to the ``dbos`` logger).
    level = logging.getLevelNamesMapping().get(settings.log_level, logging.WARNING)
    handler = LoggingHandler(level=level, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)

    _state["providers"] = (tracer_provider, logger_provider, handler)
    return True


def flush(timeout_millis: int = 10_000) -> None:
    """Export everything buffered (spans and logs). Called at exit automatically; tests call it directly."""
    if configured():
        tracer_provider, logger_provider, _ = _state["providers"]
        tracer_provider.force_flush(timeout_millis)
        logger_provider.force_flush(timeout_millis)


def scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v) for v in value)
    if isinstance(value, Mapping):
        return {k: scrub_value(v) for k, v in value.items()}
    return value


def _scrub_attribute(key: str, value: Any) -> Any:
    if key in PASS_THROUGH:
        return value
    if key.endswith("stacktrace") and isinstance(value, str):
        return scrub_stacktrace(value)
    return scrub_value(value)


def _scrub_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: _scrub_attribute(k, v) for k, v in (attributes or {}).items()}


class ScrubbingSpanExporter:
    """Wraps an exporter: identifiers are scrubbed and ``dataplat.error`` is set before spans leave."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @staticmethod
    def clean(span: Any) -> Any:
        from opentelemetry.sdk.trace import Event, ReadableSpan
        from opentelemetry.trace import Status, StatusCode

        status = span.status
        attributes = _scrub_attributes(span.attributes)
        attributes[ERROR_ATTRIBUTE] = ""
        if status.status_code is StatusCode.ERROR:
            message = status.description or ""
            status = Status(StatusCode.ERROR, scrub(message) or None)
            attributes[ERROR_ATTRIBUTE] = error_signature(message) or "error"
        events = [Event(e.name, _scrub_attributes(e.attributes), e.timestamp) for e in span.events]
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=attributes,
            events=events,
            links=span.links,
            kind=span.kind,
            status=status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def export(self, spans: Sequence[Any]) -> Any:
        return self.inner.export([self.clean(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self.inner.force_flush(timeout_millis))


class ScrubbingLogExporter:
    """Wraps a log exporter: bodies and attributes (exception message and stack trace) are scrubbed."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @staticmethod
    def clean(record: Any) -> Any:
        log_record = copy(record.log_record)
        log_record.body = scrub_value(log_record.body)
        log_record.attributes = _scrub_attributes(log_record.attributes)
        return dataclasses.replace(record, log_record=log_record)

    def export(self, batch: Sequence[Any]) -> Any:
        return self.inner.export([self.clean(r) for r in batch])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self.inner.force_flush(timeout_millis))
