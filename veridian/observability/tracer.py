"""
veridian.observability.tracer
─────────────────────────────
VeridianTracer — OpenTelemetry GenAI v1.37+ tracing with JSONL fallback.

Rules:
- OTel attributes: gen_ai.* for GenAI Semantic Conventions, veridian.* for project-specific.
- JSONL fallback: if OTel export fails, append to trace_file. NEVER lose a trace event.
- Thread-safe: concurrent record_event() calls must not corrupt the JSONL file.
- No partial writes: every JSONL append is atomic (write to tmp, os.replace).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["TraceEvent", "VeridianTracer"]


# ── OTel optional import ──────────────────────────────────────────────────────


def _get_otel_tracer(name: str) -> Any:
    """Return an OTel tracer if the SDK is installed, else None."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return None


# ── TraceEvent ────────────────────────────────────────────────────────────────


@dataclass
class TraceEvent:
    """A single structured trace event written to JSONL."""

    event_type: str
    run_id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "attributes": self.attributes,
        }


# ── VeridianTracer ────────────────────────────────────────────────────────────


class VeridianTracer:
    """
    Wraps OpenTelemetry tracing with a JSONL fallback so no trace event is ever lost.

    Usage::

        tracer = VeridianTracer(trace_file=Path("veridian_trace.jsonl"))
        tracer.start_trace(run_id="run-001")
        with tracer.trace_task(task_id="t1", task_title="My task"):
            tracer.record_event("llm_call", {"gen_ai.usage.input_tokens": 42})
        tracer.end_trace()
    """

    def __init__(
        self,
        trace_file: Path | None = None,
        use_otel: bool = True,
    ) -> None:
        self._trace_file = trace_file or Path("veridian_trace.jsonl")
        self._use_otel = use_otel
        self._run_id: str = ""
        self._lock = threading.Lock()
        self._otel_tracer = _get_otel_tracer("veridian") if use_otel else None
        self._otel_span: Any = None
        self._run_start: datetime | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_trace(self, run_id: str, attributes: dict[str, Any] | None = None) -> None:
        """Begin a new trace for the given run_id."""
        self._run_id = run_id
        self._run_start = datetime.now(UTC)

        # OTel: start root span
        if self._otel_tracer is not None:
            with contextlib.suppress(Exception):
                self._otel_span = self._otel_tracer.start_span(
                    "veridian.run",
                    attributes={
                        "gen_ai.system": "veridian",
                        "veridian.run.id": run_id,
                    },
                )

        # JSONL: always record
        self._append_event(
            TraceEvent(
                event_type="run_started",
                run_id=run_id,
                attributes={
                    "gen_ai.system": "veridian",
                    "veridian.run.id": run_id,
                    **(attributes or {}),
                },
            )
        )

    def end_trace(self, attributes: dict[str, Any] | None = None) -> None:
        """Finalise the current trace."""
        duration_ms = 0.0
        if self._run_start is not None:
            duration_ms = (datetime.now(UTC) - self._run_start).total_seconds() * 1000

        # OTel: end root span
        if self._otel_span is not None:
            with contextlib.suppress(Exception):
                self._otel_span.end()
            self._otel_span = None

        self._append_event(
            TraceEvent(
                event_type="run_completed",
                run_id=self._run_id,
                attributes={
                    "veridian.run.id": self._run_id,
                    "duration_ms": duration_ms,
                    **(attributes or {}),
                },
            )
        )

    @contextmanager
    def trace_task(
        self,
        task_id: str,
        task_title: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[None, None, None]:
        """Context manager that wraps a single task execution in a child span."""
        start = datetime.now(UTC)
        extra = {
            "veridian.task.id": task_id,
            "veridian.task.title": task_title,
            **(attributes or {}),
        }

        self._append_event(
            TraceEvent(event_type="task_start", run_id=self._run_id, attributes=extra)
        )

        try:
            yield
        finally:
            duration_ms = (datetime.now(UTC) - start).total_seconds() * 1000
            self._append_event(
                TraceEvent(
                    event_type="task_end",
                    run_id=self._run_id,
                    attributes={**extra, "duration_ms": duration_ms},
                )
            )

    def record_event(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Record an arbitrary trace event. Always falls back to JSONL on OTel failure."""
        attrs = attributes or {}

        # OTel: add span event (best-effort)
        if self._otel_span is not None:
            with contextlib.suppress(Exception):
                self._otel_span.add_event(event_type, attributes=attrs)

        # JSONL: always record (this is the guaranteed path)
        self._append_event(TraceEvent(event_type=event_type, run_id=self._run_id, attributes=attrs))

    def trace_verification(self, span: Any) -> None:
        """
        Record a single verification step as a ``verification_step`` event.

        The *span* argument must be a ``VerificationSpan`` from
        ``veridian.observability.otlp_exporter`` (accepted as ``Any`` to avoid
        a circular import).  It must expose a ``to_dict()`` method returning
        OTel-namespaced attributes:

        - ``veridian.verification.verifier_id``
        - ``veridian.verification.passed``
        - ``veridian.verification.confidence``    (optional)
        - ``veridian.verification.provenance_hash`` (optional)
        - ``veridian.verification.error``          (on failure)
        """
        attrs = span.to_dict()

        # OTel child span (best-effort)
        if self._otel_tracer is not None and self._otel_span is not None:
            with contextlib.suppress(Exception):
                child = self._otel_tracer.start_span("veridian.verification", attributes=attrs)
                child.end()

        # JSONL — always guaranteed
        self._append_event(
            TraceEvent(event_type="verification_step", run_id=self._run_id, attributes=attrs)
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append_event(self, event: TraceEvent) -> None:
        """Thread-safe append of one JSON line to the trace file."""
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._trace_file.parent.mkdir(parents=True, exist_ok=True)
            # Append-mode write — JSONL grows one line at a time.
            # Use a tmp file + os.replace to prevent torn writes on crash.
            # For append-mode this is equivalent to: read existing + write new line.
            existing = b""
            if self._trace_file.exists():
                existing = self._trace_file.read_bytes()

            new_content = existing
            if new_content and not new_content.endswith(b"\n"):
                new_content += b"\n"
            new_content += (line + "\n").encode()

            fd, tmp = tempfile.mkstemp(
                dir=self._trace_file.parent,
                prefix=".trace_",
                suffix=".tmp",
            )
            try:
                os.write(fd, new_content)
                os.close(fd)
                os.replace(tmp, self._trace_file)
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                log.exception("Failed to write trace event to JSONL: %s", event.event_type)
