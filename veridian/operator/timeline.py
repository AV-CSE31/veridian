"""
veridian.operator.timeline
────────────────────────────
Operator timeline view — structured trace of run events for human inspection.

Builds a filterable, formattable timeline from raw trace event dicts emitted
by the runner/hooks. Supports filtering by task, event type, and time range.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TimelineEntry",
    "RunTimeline",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """A single event in the operator timeline."""

    timestamp: str  # ISO-8601
    event_type: str
    task_id: str
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None


class RunTimeline:
    """Filterable, formattable timeline of run events.

    Construct via :meth:`from_events` factory, then chain filter methods
    to narrow the view before calling :meth:`format_table`.
    """

    def __init__(self, entries: list[TimelineEntry] | None = None) -> None:
        self.entries: list[TimelineEntry] = entries or []

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_events(cls, events: list[dict[str, Any]]) -> RunTimeline:
        """Build a timeline from a list of raw trace event dicts.

        Expected dict keys: ``timestamp``, ``event_type``, ``task_id``,
        ``details`` (optional dict), ``duration_ms`` (optional float).
        """
        entries: list[TimelineEntry] = []
        for ev in events:
            entry = TimelineEntry(
                timestamp=str(ev.get("timestamp", "")),
                event_type=str(ev.get("event_type", "")),
                task_id=str(ev.get("task_id", "")),
                details=dict(ev.get("details", {}) or {}),
                duration_ms=ev.get("duration_ms"),
            )
            entries.append(entry)
        return cls(entries)

    # ── Filters ──────────────────────────────────────────────────────────────

    def filter_by_task(self, task_id: str) -> RunTimeline:
        """Return a new timeline containing only events for *task_id*."""
        return RunTimeline([e for e in self.entries if e.task_id == task_id])

    def filter_by_type(self, event_type: str) -> RunTimeline:
        """Return a new timeline containing only events of *event_type*."""
        return RunTimeline([e for e in self.entries if e.event_type == event_type])

    def time_range(self, start: str, end: str) -> RunTimeline:
        """Return a new timeline with events whose timestamp is in [start, end].

        Comparison is lexicographic on ISO-8601 strings, which is correct for
        UTC timestamps with identical formatting.
        """
        return RunTimeline([e for e in self.entries if start <= e.timestamp <= end])

    # ── Formatting ───────────────────────────────────────────────────────────

    def format_table(self) -> str:
        """Render the timeline as a human-readable table string."""
        col_ts = "timestamp"
        col_et = "event_type"
        col_tid = "task_id"
        col_dur = "duration_ms"
        col_det = "details"

        if not self.entries:
            return f"{col_ts:<28} {col_et:<20} {col_tid:<12} {col_dur:<14} {col_det}"

        # Compute column widths
        w_ts = max(len(col_ts), *(len(e.timestamp) for e in self.entries))
        w_et = max(len(col_et), *(len(e.event_type) for e in self.entries))
        w_tid = max(len(col_tid), *(len(e.task_id) for e in self.entries))
        w_dur = max(len(col_dur), 14)

        header = (
            f"{col_ts:<{w_ts}} {col_et:<{w_et}} {col_tid:<{w_tid}} {col_dur:<{w_dur}} {col_det}"
        )
        sep = "-" * len(header)
        lines = [header, sep]

        for e in self.entries:
            dur_str = f"{e.duration_ms:.1f}" if e.duration_ms is not None else "-"
            det_str = str(e.details) if e.details else ""
            lines.append(
                f"{e.timestamp:<{w_ts}} {e.event_type:<{w_et}} "
                f"{e.task_id:<{w_tid}} {dur_str:<{w_dur}} {det_str}"
            )

        return "\n".join(lines)
