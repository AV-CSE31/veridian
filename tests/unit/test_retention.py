"""
Tests for veridian.observability.retention — Retention policies for JSONL trace files.
TDD: RED phase (WCP-022).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from veridian.observability.retention import RetentionManager, RetentionPolicy

# ── RetentionPolicy construction ───────────────────────────────────────────────


class TestRetentionPolicyConstruction:
    def test_default_no_limits(self) -> None:
        policy = RetentionPolicy()
        assert policy.max_age_hours is None
        assert policy.max_size_mb is None
        assert policy.max_events is None

    def test_custom_limits(self) -> None:
        policy = RetentionPolicy(max_age_hours=24, max_size_mb=10.0, max_events=5000)
        assert policy.max_age_hours == 24
        assert policy.max_size_mb == 10.0
        assert policy.max_events == 5000


# ── max_age_hours ──────────────────────────────────────────────────────────────


class TestRetentionMaxAge:
    def test_removes_events_older_than_ttl(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        now = time.time()
        events = [
            {"timestamp": now - 7200, "event_type": "old"},  # 2 hours ago
            {"timestamp": now - 60, "event_type": "recent"},  # 1 min ago
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        policy = RetentionPolicy(max_age_hours=1)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)

        remaining = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(remaining) == 1
        assert remaining[0]["event_type"] == "recent"

    def test_keeps_all_if_none_expired(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        now = time.time()
        events = [
            {"timestamp": now - 60, "event_type": "a"},
            {"timestamp": now - 30, "event_type": "b"},
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        policy = RetentionPolicy(max_age_hours=1)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)

        remaining = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(remaining) == 2


# ── max_size_mb ────────────────────────────────────────────────────────────────


class TestRetentionMaxSize:
    def test_removes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        # Each event is roughly 40-60 bytes; create enough to exceed a tiny limit
        events = [{"i": i, "data": "x" * 100} for i in range(100)]
        trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        original_size = trace_file.stat().st_size
        # Set a limit far smaller than current file
        limit_mb = original_size / (1024 * 1024 * 4)  # quarter of current size
        policy = RetentionPolicy(max_size_mb=limit_mb)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)

        new_size = trace_file.stat().st_size
        assert new_size <= limit_mb * 1024 * 1024 * 1.1  # small tolerance
        remaining = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(remaining) < 100
        # Remaining events should be the most recent ones (highest i values)
        assert remaining[-1]["i"] == 99


# ── max_events ─────────────────────────────────────────────────────────────────


class TestRetentionMaxEvents:
    def test_enforces_max_events_count(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        events = [{"i": i} for i in range(50)]
        trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        policy = RetentionPolicy(max_events=20)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)

        remaining = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(remaining) == 20
        # Should keep the most recent (last 20)
        assert remaining[0]["i"] == 30
        assert remaining[-1]["i"] == 49


# ── Combined policies ──────────────────────────────────────────────────────────


class TestRetentionCombined:
    def test_applies_all_policies(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        now = time.time()
        events = [
            {"timestamp": now - 7200, "i": 0},  # old — removed by age
            {"timestamp": now - 60, "i": 1},
            {"timestamp": now - 30, "i": 2},
            {"timestamp": now - 10, "i": 3},
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        policy = RetentionPolicy(max_age_hours=1, max_events=2)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)

        remaining = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        # Age removes event i=0. Then max_events=2 keeps last 2 of [1,2,3] -> [2,3]
        assert len(remaining) == 2
        assert remaining[0]["i"] == 2
        assert remaining[1]["i"] == 3


# ── Empty file handling ────────────────────────────────────────────────────────


class TestRetentionEmptyFile:
    def test_empty_file_is_noop(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "trace.jsonl"
        trace_file.write_text("")
        policy = RetentionPolicy(max_events=10)
        manager = RetentionManager(policy=policy)
        manager.enforce(trace_file)
        assert trace_file.read_text() == ""

    def test_nonexistent_file_is_noop(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "nonexistent.jsonl"
        policy = RetentionPolicy(max_events=10)
        manager = RetentionManager(policy=policy)
        # Should not raise
        manager.enforce(trace_file)
        assert not trace_file.exists()
