"""Observability: scrubbed OpenTelemetry export (``telemetry``), alert definitions (``alerts``) and the
OpenObserve API client (``openobserve``). Everything here is off unless the environment turns it on."""

from dataplat.observe.scrub import error_signature, scrub

__all__ = ["error_signature", "scrub"]
