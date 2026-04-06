"""
tests.unit.test_operator_timeline
───────────────────────────────────
Unit tests for the operator timeline view.

Proves:
- Build timeline from list of trace event dicts
- Filter by task_id, event_type, time_range
- Human-readable formatting
- Empty timeline handling
"""

from __future__ import annotations

from veridian.operator.timeline import RunTimeline

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _sample_events() -> list[dict[str, object]]:
    return [
        {
            "timestamp": "2026-04-01T10:00:00Z",
            "event_type": "task_claimed",
            "task_id": "t-001",
            "details": {"agent": "worker-1"},
            "duration_ms": 12.5,
        },
        {
            "timestamp": "2026-04-01T10:01:00Z",
            "event_type": "task_completed",
            "task_id": "t-001",
            "details": {"result": "pass"},
            "duration_ms": 500.0,
        },
        {
            "timestamp": "2026-04-01T10:02:00Z",
            "event_type": "task_claimed",
            "task_id": "t-002",
            "details": {"agent": "worker-2"},
        },
        {
            "timestamp": "2026-04-01T10:05:00Z",
            "event_type": "task_failed",
            "task_id": "t-002",
            "details": {"error": "timeout"},
            "duration_ms": 3000.0,
        },
    ]


# ── Build from events ────────────────────────────────────────────────────────


class TestFromEvents:
    def test_builds_all_entries(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        assert len(tl.entries) == 4

    def test_entry_fields_populated(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        entry = tl.entries[0]
        assert entry.timestamp == "2026-04-01T10:00:00Z"
        assert entry.event_type == "task_claimed"
        assert entry.task_id == "t-001"
        assert entry.details == {"agent": "worker-1"}
        assert entry.duration_ms == 12.5

    def test_optional_duration_defaults_none(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        entry = tl.entries[2]  # t-002 claimed — no duration_ms
        assert entry.duration_ms is None


# ── Filter by task_id ────────────────────────────────────────────────────────


class TestFilterByTask:
    def test_returns_matching_entries(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.filter_by_task("t-001")
        assert len(filtered.entries) == 2
        assert all(e.task_id == "t-001" for e in filtered.entries)

    def test_no_match_returns_empty(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.filter_by_task("nonexistent")
        assert len(filtered.entries) == 0


# ── Filter by event_type ────────────────────────────────────────────────────


class TestFilterByType:
    def test_returns_matching_type(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.filter_by_type("task_claimed")
        assert len(filtered.entries) == 2
        assert all(e.event_type == "task_claimed" for e in filtered.entries)

    def test_no_match_returns_empty(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.filter_by_type("unknown_event")
        assert len(filtered.entries) == 0


# ── Filter by time range ────────────────────────────────────────────────────


class TestTimeRange:
    def test_inclusive_range(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.time_range("2026-04-01T10:00:00Z", "2026-04-01T10:01:00Z")
        assert len(filtered.entries) == 2
        for e in filtered.entries:
            assert e.timestamp >= "2026-04-01T10:00:00Z"
            assert e.timestamp <= "2026-04-01T10:01:00Z"

    def test_range_excludes_outside(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        filtered = tl.time_range("2026-04-01T10:03:00Z", "2026-04-01T10:06:00Z")
        assert len(filtered.entries) == 1
        assert filtered.entries[0].task_id == "t-002"


# ── Formatting ───────────────────────────────────────────────────────────────


class TestFormatTable:
    def test_non_empty_produces_header(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        table = tl.format_table()
        assert "timestamp" in table.lower()
        assert "event_type" in table.lower()
        assert "task_id" in table.lower()

    def test_contains_entry_data(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        table = tl.format_table()
        assert "t-001" in table
        assert "task_claimed" in table

    def test_returns_string(self) -> None:
        tl = RunTimeline.from_events(_sample_events())
        assert isinstance(tl.format_table(), str)


# ── Empty timeline ───────────────────────────────────────────────────────────


class TestEmptyTimeline:
    def test_from_empty_list(self) -> None:
        tl = RunTimeline.from_events([])
        assert len(tl.entries) == 0

    def test_format_table_empty(self) -> None:
        tl = RunTimeline.from_events([])
        table = tl.format_table()
        assert isinstance(table, str)
        # Should still produce a valid string, possibly with header only
        assert len(table) >= 0

    def test_filter_on_empty(self) -> None:
        tl = RunTimeline.from_events([])
        assert len(tl.filter_by_task("t-001").entries) == 0
        assert len(tl.filter_by_type("any").entries) == 0
        assert len(tl.time_range("2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z").entries) == 0
