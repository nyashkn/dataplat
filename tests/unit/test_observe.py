from __future__ import annotations

import base64

import pytest

from dataplat.observe import error_signature, scrub
from dataplat.observe.scrub import scrub_stacktrace
from dataplat.observe.telemetry import TelemetrySettings, parse_headers


@pytest.mark.parametrize(
    ("raw", "planted"),
    [
        ("payout failed for +254712345678", "254712345678"),
        ("float artifact 254712345678.0 in phone", "254712345678"),
        ("national id 12345678 rejected", "12345678"),
        ("call 0712 345 678 now", "0712 345 678"),
        ("could not convert string '12a' to INT32", "'12a'"),
        ('values: ["0712345678", "x"]', "0712345678"),
        ("contact jane.doe@example.co.ke", "jane.doe@example.co.ke"),
        ("token 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "9f86d081884c7d659a2f"),
        ("user 343970a0-cd81-4fec-b36e-391e7a48b52e not found", "343970a0-cd81"),
    ],
)
def test_scrub_catches_each_planted_identifier(raw: str, planted: str) -> None:
    cleaned = scrub(raw)
    assert planted not in cleaned
    assert scrub(cleaned) == cleaned  # idempotent


@pytest.mark.parametrize(
    "keep",
    [
        "partition 2026-09-01 missing (expected=9, missing=6)",
        "600 rows at 03:20:00 in column 'amount'",
        'IO Error: cannot open "/lake/usage/event_date=2026-09-01/part-0.parquet"',
        "shapes {'+9{12}': 17}",
    ],
)
def test_scrub_keeps_what_explains_the_error(keep: str) -> None:
    assert scrub(keep) == keep


def test_stacktrace_keeps_code_and_scrubs_the_exception_line() -> None:
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/srv/app-2/src/pipelines/payout.py", line 21, in charge\n'
        '    raise ValueError(f"payout failed for {msisdn}")\n'
        "ValueError: payout failed for 254712345678 ('0712345678')"
    )
    cleaned = scrub_stacktrace(trace)
    assert '  File "/srv/app-2/src/pipelines/payout.py", line 21, in charge' in cleaned
    assert '    raise ValueError(f"payout failed for {msisdn}")' in cleaned
    assert cleaned.endswith("ValueError: payout failed for 9{12} ('9{10}')")
    long = "\n".join(f"    frame {i}" for i in range(5000)) + "\nValueError: 254712345678"
    assert scrub_stacktrace(long).endswith("ValueError: 9{12}")


def test_error_signature_groups_repeats() -> None:
    a = error_signature("partition 2026-09-01 missing for workflow 343970a0-cd81-4fec-b36e-391e7a48b52e")
    b = error_signature("partition 2026-09-02 missing for workflow 11111111-2222-3333-4444-555555555555")
    assert a == b == "partition #-#-# missing for workflow <uuid>"
    assert error_signature("") == ""
    assert error_signature("first line 12\nsecond line") == "first line #"
    assert error_signature("DBOS Error 7: step exceeded 3 retries") == "DBOS Error #: step exceeded # retries"
    assert error_signature("payout failed for +254712345678 on INT32") == "payout failed for +9{12} on INT32"


def test_telemetry_is_off_unless_the_environment_turns_it_on() -> None:
    assert TelemetrySettings.from_env({}) is None
    on = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://o2:5080/api/default/"}
    assert TelemetrySettings.from_env({**on, "DATAPLAT_TELEMETRY": "off"}) is None
    s = TelemetrySettings.from_env(on, default_stream="mdundo")
    assert s is not None
    assert s.traces_endpoint == "http://o2:5080/api/default/v1/traces"
    assert s.logs_endpoint == "http://o2:5080/api/default/v1/logs"
    assert s.headers == {"stream-name": "mdundo"}


def test_telemetry_credentials_and_stream() -> None:
    env = {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces",
        "OPENOBSERVE_USER": "ops@example.org",
        "OPENOBSERVE_PASSWORD": "pw",
        "DATAPLAT_TELEMETRY_STREAM": "dockblocks",
    }
    s = TelemetrySettings.from_env(env, default_stream="ignored")
    assert s is not None and s.logs_endpoint is None
    assert s.headers["Authorization"] == "Basic " + base64.b64encode(b"ops@example.org:pw").decode()
    assert s.headers["stream-name"] == "dockblocks"
    assert "pw" not in repr(s) and "Basic" not in repr(s)
    explicit = TelemetrySettings.from_env(
        {**env, "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20abc,stream-name=raw"}
    )
    assert explicit is not None and dict(explicit.headers) == {"Authorization": "Basic abc", "stream-name": "raw"}


def test_parse_headers_follows_the_spec() -> None:
    assert parse_headers("a=1, b = two%20words,broken,=x") == {"a": "1", "b": "two words"}
