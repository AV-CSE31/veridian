"""veridian.observability — Tracing, dashboard, and audit tooling."""

from veridian.observability.dashboard import DASHBOARD_PORT, VeridianDashboard
from veridian.observability.otlp_exporter import (
    OTLPConfig,
    VerificationSpan,
    configure_otlp_tracer,
)
from veridian.observability.tracer import TraceEvent, VeridianTracer

__all__ = [
    "TraceEvent",
    "VeridianTracer",
    "VeridianDashboard",
    "DASHBOARD_PORT",
    # A2: OTLP exporter
    "OTLPConfig",
    "VerificationSpan",
    "configure_otlp_tracer",
]
