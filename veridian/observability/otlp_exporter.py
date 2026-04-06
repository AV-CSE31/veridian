"""
veridian.observability.otlp_exporter
─────────────────────────────────────
A2: Native OTLP export of verification traces.

Each verification step is recorded as a structured event with:
  - veridian.verification.verifier_id   : the verifier that ran
  - veridian.verification.passed        : bool pass/fail
  - veridian.verification.confidence    : float 0.0-1.0 (optional)
  - veridian.verification.provenance_hash : SHA-256 of (task_id + result)
  - veridian.verification.error         : failure message (optional)

Usage::

    from veridian.observability.otlp_exporter import OTLPConfig, configure_otlp_tracer

    tracer = configure_otlp_tracer(
        config=OTLPConfig(endpoint="http://otel-collector:4318/v1/traces"),
    )
    tracer.start_trace(run_id="run-001")
    tracer.trace_verification(
        VerificationSpan(task_id="t1", verifier_id="schema", passed=True, confidence=0.95)
    )
    tracer.end_trace()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.observability.tracer import VeridianTracer

log = logging.getLogger(__name__)

__all__ = ["OTLPConfig", "VerificationSpan", "configure_otlp_tracer"]


# ── OTLPConfig ────────────────────────────────────────────────────────────────


@dataclass
class OTLPConfig:
    """Configuration for the OTLP HTTP exporter."""

    endpoint: str = "http://localhost:4318/v1/traces"
    service_name: str = "veridian"
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 10


# ── VerificationSpan ──────────────────────────────────────────────────────────


@dataclass
class VerificationSpan:
    """
    Data for a single verification step span.

    Attributes
    ----------
    task_id:
        The task being verified.
    verifier_id:
        ID of the verifier that ran (e.g. ``"schema"``, ``"bash_exit"``).
    passed:
        Whether this verifier passed.
    confidence:
        Optional confidence score 0.0-1.0 (used by LLMJudgeVerifier, etc.)
    provenance_hash:
        Optional SHA-256 binding task_id + result snapshot.
    error:
        Error message when ``passed=False``.
    """

    task_id: str
    verifier_id: str
    passed: bool
    confidence: float | None = None
    provenance_hash: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return OTel-style attribute dict (omits None values)."""
        d: dict[str, Any] = {
            "veridian.task.id": self.task_id,
            "veridian.verification.verifier_id": self.verifier_id,
            "veridian.verification.passed": self.passed,
        }
        if self.confidence is not None:
            d["veridian.verification.confidence"] = self.confidence
        if self.provenance_hash is not None:
            d["veridian.verification.provenance_hash"] = self.provenance_hash
        if self.error is not None:
            d["veridian.verification.error"] = self.error
        return d


# ── configure_otlp_tracer ─────────────────────────────────────────────────────


def configure_otlp_tracer(
    config: OTLPConfig | None = None,
    trace_file: Path | None = None,
    use_otel: bool = True,
) -> VeridianTracer:
    """
    Create and return a ``VeridianTracer`` configured with an OTLP HTTP exporter.

    If the ``opentelemetry-sdk`` / ``opentelemetry-exporter-otlp-proto-http``
    packages are not installed, degrades gracefully to JSONL-only mode.

    Parameters
    ----------
    config:
        OTLP exporter settings.  Defaults to ``OTLPConfig()``.
    trace_file:
        Path for the JSONL fallback trace file.
    use_otel:
        Pass ``False`` to skip OTel SDK initialisation (useful in tests).
    """
    cfg = config or OTLPConfig()

    if use_otel:
        _try_configure_sdk(cfg)

    return VeridianTracer(
        trace_file=trace_file or Path("veridian_trace.jsonl"),
        use_otel=use_otel,
    )


def _try_configure_sdk(cfg: OTLPConfig) -> None:
    """Best-effort OTLP SDK setup.  Never raises — failures are logged."""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
        )

        resource = Resource.create({"service.name": cfg.service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=cfg.endpoint,
            headers=cfg.headers,
            timeout=cfg.timeout_seconds,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        log.info("otlp_exporter: configured OTLP at %s", cfg.endpoint)

    except ImportError:
        log.debug(
            "otlp_exporter: opentelemetry SDK not installed — JSONL fallback only. "
            "Install with: pip install 'veridian-ai[otel]'"
        )
    except Exception:
        log.exception("otlp_exporter: SDK config failed — JSONL fallback only")
