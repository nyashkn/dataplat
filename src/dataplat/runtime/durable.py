"""DBOS helpers: configuration, deterministic workflow ids, namespaced queues.

Pipelines are ordinary DBOS workflows declared in ``<pkg>.pipelines``. The pattern::

    @DBOS.step(retries_allowed=True, max_attempts=3)
    def build_and_commit(day: str) -> dict:  # compute + ONE lake transaction
        ...
        return asdict(writer.commit_partition(...))


    @DBOS.workflow()
    def usage_daily(day: str) -> dict:
        return build_and_commit(day)


    with SetWorkflowID(workflow_id("usage_daily", day)):
        usage_daily(day)  # a second call with the same id does not re-run

The step is atomic (one DuckLake transaction) and idempotent (partition overwrite), so DBOS retries are
safe. Use a different ``attempt`` tag to deliberately recompute a partition that already succeeded.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from dataplat import isolation

Role = Literal["worker", "cli"]

_ID_PART = re.compile(r"^[A-Za-z0-9_.\-]+$")


def dbos_config(
    app_name: str,
    *,
    system_database_url: str,
    code_version: str,
    namespace: str = isolation.MAIN,
    role: Role = "worker",
    log_level: str = "WARNING",
) -> dict[str, Any]:
    """Config dict for ``DBOS(config=...)``.

    - ``application_version`` is the code version, so DBOS recovers workflows only on the code that
      started them.
    - ``role="worker"``: stable executor id ``worker``. It recovers its own interrupted workflows on
      restart and executes queued work (scheduled runs, proposed actions).
    - ``role="cli"``: a fresh executor id per process, so a short-lived command never recovers or steals
      the worker's workflows. Pair it with ``launch(..., role="cli")``, which listens to no queues.
    - On Postgres, each namespace gets its own system schema.
    """
    name = app_name if namespace == isolation.MAIN else f"{app_name}-{namespace}"
    cfg: dict[str, Any] = {
        "name": name,
        "system_database_url": system_database_url,
        "application_version": code_version,
        "executor_id": "worker" if role == "worker" else f"cli-{uuid.uuid4().hex[:10]}",
        "log_level": log_level,
    }
    if system_database_url.startswith("postgres"):
        cfg["dbos_system_schema"] = "dbos" if namespace == isolation.MAIN else f"dbos_{namespace}"
    return cfg


def launch(config: dict[str, Any], *, role: Role = "worker") -> None:
    """Configure and launch DBOS for this process. CLI processes dequeue nothing.

    When the environment configures telemetry (``dataplat.observe.telemetry``), the scrubbing trace and
    log providers are installed first and DBOS's own OpenTelemetry support is switched on, so every
    workflow and step becomes a span and DBOS's error logs are exported with it.
    """
    from dbos import DBOS

    from dataplat.observe import telemetry

    settings = telemetry.TelemetrySettings.from_env()
    if settings is not None:
        namespace = isolation.current_namespace()
        name = str(config["name"])
        project = name.removesuffix(f"-{namespace}") if namespace != isolation.MAIN else name
        settings = telemetry.TelemetrySettings.from_env(default_stream=project) or settings
        telemetry.configure(
            settings,
            service_name=name,
            service_version=str(config.get("application_version", "dev")),
            role=role,
            namespace=namespace,
        )
        config = {**config, "enable_otlp": True, "otel_attribute_format": "semconv"}
    DBOS(config=config)  # type: ignore[arg-type]
    if role == "cli":
        DBOS.listen_queues([])
    DBOS.launch()


def workflow_id(pipeline: str, *keys: object, attempt: str | None = None) -> str:
    """``usage_daily:2026-09-01`` (+ ``:attempt``). Same inputs, same id, at-most-once execution."""
    parts = [pipeline, *(str(k) for k in keys)] + ([attempt] if attempt else [])
    for p in parts:
        if not _ID_PART.match(p):
            raise ValueError(f"workflow id part {p!r} must match {_ID_PART.pattern}")
    return ":".join(parts)


def queue_name(base: str, namespace: str | None = None) -> str:
    return isolation.queue_name(base, namespace or isolation.current_namespace())
