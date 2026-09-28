"""Alerts as code: what should reach a human, derived from DBOS spans and the project's schedules.

Every alert is a scheduled SQL alert over the project's traces stream, and every one is count-based:
each row the query returns is one thing to act on, so all of them trigger on ``>= 1`` row.

- ``<project>_workflow_failures``: failed workflows in the last 15 minutes, one row per workflow and
  error signature (``dataplat.error``). Silenced for an hour after it fires, so a failure that keeps
  happening reports hourly, not on every run.
- ``<project>_new_errors``: a workflow error signature first seen in the last 15 minutes, looking back
  seven days. The failures alert says "still broken"; this one says "broken in a new way".
- ``<project>_heartbeat_<schedule>``: no successful run of a scheduled workflow within its cron interval
  plus a grace period (a twelfth of the interval, at least an hour): the run that never happened.

Only ``main`` pages: worktree namespaces report under their own ``service.name``. Column names follow
OpenObserve's flattening of OTLP spans (``dbos.operation.type`` becomes ``dbos_operation_type``). The
exporter sets ``dataplat.error`` on every span, so that column exists from the first span on.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dataplat.errors import ConfigError
from dataplat.observe.telemetry import stream_name

FAILURE_WINDOW_MINUTES = 15
NEW_ERROR_LOOKBACK_MINUTES = 7 * 24 * 60
MIN_GRACE_MINUTES = 60
_SAFE = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_NAME = re.compile(r"^[a-z0-9_]+$")


def _literal(value: str) -> str:
    if not _SAFE.match(value):
        raise ConfigError(f"{value!r} cannot be used in an alert query: letters, digits and _.:- only")
    return f"'{value}'"


@dataclass(frozen=True)
class Alert:
    name: str
    description: str
    sql: str
    period_minutes: int
    frequency_minutes: int
    silence_minutes: int
    stream: str
    stream_type: str = "traces"
    threshold: int = 1
    operator: str = ">="

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ConfigError(f"alert name {self.name!r} must be snake_case (a-z, 0-9, _)")

    def payload(self, destinations: Sequence[str]) -> dict[str, Any]:
        """The body of OpenObserve's ``POST /api/v2/{org}/alerts`` and ``PUT .../alerts/{id}``."""
        return {
            "name": self.name,
            "stream_type": self.stream_type,
            "stream_name": self.stream,
            "is_real_time": False,
            "query_condition": {"type": "sql", "sql": self.sql},
            "trigger_condition": {
                "period": self.period_minutes,
                "operator": self.operator,
                "threshold": self.threshold,
                "frequency": self.frequency_minutes,
                "frequency_type": "minutes",
                "silence": self.silence_minutes,
            },
            "destinations": list(destinations),
            "description": self.description,
            "enabled": True,
        }


def _failed_workflows(stream: str, service: str) -> str:
    return (
        f'FROM "{stream}" WHERE service_name = {_literal(service)} '
        "AND dbos_operation_type = 'workflow' AND span_status = 'ERROR' "
        "GROUP BY operation_name, dataplat_error"
    )


def workflow_failures(stream: str, service: str, prefix: str) -> Alert:
    return Alert(
        name=f"{prefix}_workflow_failures",
        description=f"Workflows of {service} that failed in the last {FAILURE_WINDOW_MINUTES} minutes, "
        "one row per workflow and error signature.",
        sql="SELECT operation_name, dataplat_error, COUNT(*) AS failures, MAX(_timestamp) AS last_seen "
        + _failed_workflows(stream, service),
        period_minutes=FAILURE_WINDOW_MINUTES,
        frequency_minutes=FAILURE_WINDOW_MINUTES,
        silence_minutes=60,
        stream=stream,
    )


def new_errors(stream: str, service: str, prefix: str) -> Alert:
    recent_micros = f"(CAST(EXTRACT(EPOCH FROM NOW()) AS BIGINT) - {FAILURE_WINDOW_MINUTES * 60}) * 1000000"
    return Alert(
        name=f"{prefix}_new_errors",
        description=f"A workflow error signature of {service} first seen in the last "
        f"{FAILURE_WINDOW_MINUTES} minutes, with nothing like it in the seven days before.",
        sql="SELECT operation_name, dataplat_error, COUNT(*) AS failures, MIN(_timestamp) AS first_seen "
        + _failed_workflows(stream, service)
        + f" HAVING MIN(_timestamp) >= {recent_micros}",
        period_minutes=NEW_ERROR_LOOKBACK_MINUTES + FAILURE_WINDOW_MINUTES,
        frequency_minutes=FAILURE_WINDOW_MINUTES,
        silence_minutes=FAILURE_WINDOW_MINUTES,
        stream=stream,
    )


def cron_interval_minutes(cron: str, *, samples: int = 400) -> int:
    """The longest gap between two consecutive fires of ``cron`` (UTC), in minutes."""
    from dbos._croniter import croniter  # type: ignore[attr-defined]  # the parser DBOS schedules with

    it = croniter(cron, datetime(2026, 1, 1, tzinfo=UTC))
    fires = [it.get_next(datetime) for _ in range(samples)]
    return int(max((b - a).total_seconds() for a, b in zip(fires, fires[1:], strict=False)) // 60)


def heartbeat(stream: str, service: str, prefix: str, schedule: str, workflow: str, cron: str) -> Alert:
    interval = cron_interval_minutes(cron)
    grace = max(MIN_GRACE_MINUTES, interval // 12)
    return Alert(
        name=f"{prefix}_heartbeat_{stream_name(schedule)}",
        description=f"No successful {workflow} run (schedule {schedule}, {cron} UTC) in the last "
        f"{interval + grace} minutes.",
        sql=f'SELECT COUNT(*) AS succeeded FROM "{stream}" WHERE service_name = {_literal(service)} '
        f"AND operation_name = {_literal(workflow)} AND span_status = 'OK' HAVING COUNT(*) = 0",
        period_minutes=interval + grace,
        frequency_minutes=min(60, max(FAILURE_WINDOW_MINUTES, interval // 4)),
        silence_minutes=max(60, interval // 4),
        stream=stream,
    )


def workflow_name(fn: Callable[..., Any]) -> str:
    """The name DBOS gives a workflow's spans (its registered function name)."""
    return str(getattr(fn, "dbos_function_name", None) or getattr(fn, "__qualname__", repr(fn)))


def standard_alerts(project: str, schedules: Sequence[Mapping[str, Any]] = ()) -> list[Alert]:
    """Failures, new errors and one heartbeat per schedule for ``project`` (its ``main`` namespace).

    ``schedules`` are DBOS ``ScheduleInput`` dicts (``schedule_name``, ``workflow_fn``, ``schedule``).
    """
    stream = stream_name(project)
    alerts = [workflow_failures(stream, project, stream), new_errors(stream, project, stream)]
    for s in schedules:
        alerts.append(
            heartbeat(stream, project, stream, s["schedule_name"], workflow_name(s["workflow_fn"]), s["schedule"])
        )
    return alerts
