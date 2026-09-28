# 0009. Telemetry: OpenTelemetry out of DBOS, scrubbed, into OpenObserve

- Status: accepted

## Context
Pipelines and agents run unattended on the box. Someone has to learn that a workflow failed, that a
scheduled run never happened, and why. DBOS already instruments every workflow and step with
OpenTelemetry, Claude Code exports OpenTelemetry, and so does xorq if it is ever adopted. Exception
messages can quote the values they failed on, and the lake holds phone numbers and national ids.

Compared: GlitchTip (Sentry-compatible error tracking: issue grouping, regressions, heartbeats; takes
OpenTelemetry logs only as of 6.2), Parseable (open-source edition lacks traces, PromQL and HA) and
OpenObserve (logs, metrics and traces in the open-source edition; Parquet on S3-compatible storage with
its own file index, so RustFS's truncated listings don't affect queries).

## Decision
- One tool from day one: OpenObserve, fed over OTLP/HTTP. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is
  set; tests force it off.
- dataplat owns the trace and log providers (`dataplat.observe.telemetry`) and installs them before
  DBOS starts, so DBOS's spans and error logs pass through scrubbing exporters: emails, UUIDs, 7+ digit
  runs and quoted values become shapes; stack traces keep code lines and lose values. Every span carries
  `dataplat.error`, the failure's signature, which the alerts group by.
- Alerts are code (`dataplat.observe.alerts`): failed workflows, error signatures new in 7 days, and a
  heartbeat per DBOS schedule. `apply` upserts them by name and never deletes.

## Consequences
- What GlitchTip would have given for free is now a query: repeats are grouped by signature, "new" and
  "regressed" by the 7-day lookback, heartbeats by an absence query per schedule.
- SSO and role-based access are Enterprise features in OpenObserve (free under 50 GB/day, not open
  source). Fine while the operators are few; revisit when a client team needs scoped access.
- Scrubbing is a backstop. Code still writes counts and shapes into messages (CONVENTIONS rule 29).
- A user-facing app with many users would still earn a Sentry-style tracker; this decision covers
  pipelines and agents.
