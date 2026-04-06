"""
veridian.secrets.trace_filter
─────────────────────────────
WCP-026: Trace-level PII filtering for observability events.
"""

from __future__ import annotations

from typing import Any

from veridian.secrets.pii_policy import PIIPolicy

__all__ = ["TraceFilter"]


class TraceFilter:
    """Recursively sanitize trace events using a PIIPolicy."""

    def __init__(self, policy: PIIPolicy | None = None) -> None:
        self._policy = policy or PIIPolicy()

    def filter_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Return a sanitized copy of the trace event."""
        sanitized = self._sanitize_value(event)
        if isinstance(sanitized, dict):
            return sanitized
        return {}

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._policy.redact(value)
        if isinstance(value, dict):
            return {k: self._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_value(v) for v in value)
        return value
