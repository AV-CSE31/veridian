"""
tests/unit/test_dashboard_data.py
───────────────────────────────────
Unit tests for the Compliance Dashboard Data Layer (F2.4).

Covers:
  - VerificationRecord: creation
  - ComplianceDashboard: add_record, pass_rate, time_series
  - Per-verifier stats: pass rate, failure count
  - Per-agent stats: pass rate, task count
  - Export: JSON, CSV
  - Summary report generation
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from veridian.dashboard.data_layer import (
    AgentStats,
    ComplianceDashboard,
    TimeSeriesPoint,
    VerificationRecord,
    VerifierStats,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _rec(
    *,
    task_id: str = "t1",
    verifier_id: str = "schema",
    agent_id: str | None = "agent-A",
    passed: bool = True,
    error: str | None = None,
    response_time_ms: float = 10.0,
    failure_category: str | None = None,
    offset_hours: int = 0,
) -> VerificationRecord:
    return VerificationRecord(
        task_id=task_id,
        verifier_id=verifier_id,
        agent_id=agent_id,
        timestamp=datetime(2026, 1, 1, 12 + offset_hours, 0, 0, tzinfo=timezone.utc),
        passed=passed,
        error=error,
        response_time_ms=response_time_ms,
        failure_category=failure_category,
    )


@pytest.fixture
def dashboard_with_records() -> ComplianceDashboard:
    dash = ComplianceDashboard()
    # 7 passed, 3 failed across 2 verifiers and 2 agents
    for i in range(7):
        dash.add_record(_rec(task_id=f"t{i}", verifier_id="schema", agent_id="agent-A", passed=True))
    dash.add_record(_rec(task_id="t7", verifier_id="schema", agent_id="agent-A", passed=False,
                         error="schema mismatch", failure_category="schema_error"))
    dash.add_record(_rec(task_id="t8", verifier_id="tool_safety", agent_id="agent-B",
                         passed=False, error="shell=True", failure_category="tool_safety"))
    dash.add_record(_rec(task_id="t9", verifier_id="tool_safety", agent_id="agent-B",
                         passed=True))
    return dash


# ─────────────────────────────────────────────────────────────────────────────
# VerificationRecord
# ─────────────────────────────────────────────────────────────────────────────


class TestVerificationRecord:
    def test_record_creation(self) -> None:
        rec = _rec()
        assert rec.task_id == "t1"
        assert rec.verifier_id == "schema"
        assert rec.passed is True

    def test_record_with_failure(self) -> None:
        rec = _rec(passed=False, error="field missing", failure_category="schema_error")
        assert rec.passed is False
        assert rec.error == "field missing"
        assert rec.failure_category == "schema_error"


# ─────────────────────────────────────────────────────────────────────────────
# ComplianceDashboard — core metrics
# ─────────────────────────────────────────────────────────────────────────────


class TestComplianceDashboard:
    def test_empty_dashboard(self) -> None:
        dash = ComplianceDashboard()
        assert dash.total_records == 0
        assert dash.pass_rate() == 0.0

    def test_add_record_increments_count(self) -> None:
        dash = ComplianceDashboard()
        dash.add_record(_rec())
        assert dash.total_records == 1

    def test_pass_rate_all_passing(self) -> None:
        dash = ComplianceDashboard()
        for _ in range(5):
            dash.add_record(_rec(passed=True))
        assert abs(dash.pass_rate() - 1.0) < 1e-9

    def test_pass_rate_mixed(self, dashboard_with_records: ComplianceDashboard) -> None:
        # 8 passed / 10 total = 0.8
        assert abs(dashboard_with_records.pass_rate() - 0.8) < 1e-9

    def test_pass_rate_since_filters_by_time(self) -> None:
        dash = ComplianceDashboard()
        # 3 old passed
        for i in range(3):
            dash.add_record(_rec(passed=True, offset_hours=0))
        # 2 new failed
        cutoff = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
        for i in range(2):
            dash.add_record(_rec(passed=False, offset_hours=3))
        rate_recent = dash.pass_rate(since=cutoff)
        assert abs(rate_recent - 0.0) < 1e-9  # only failures after cutoff

    def test_failure_categories(self, dashboard_with_records: ComplianceDashboard) -> None:
        cats = dashboard_with_records.failure_categories()
        assert cats.get("schema_error", 0) >= 1
        assert cats.get("tool_safety", 0) >= 1

    def test_total_records_count(self, dashboard_with_records: ComplianceDashboard) -> None:
        assert dashboard_with_records.total_records == 10


# ─────────────────────────────────────────────────────────────────────────────
# Time-series
# ─────────────────────────────────────────────────────────────────────────────


class TestTimeSeries:
    def test_time_series_buckets_by_hour(self) -> None:
        dash = ComplianceDashboard()
        # 5 records at hour 12, 5 at hour 13
        for _ in range(5):
            dash.add_record(_rec(passed=True, offset_hours=0))
        for _ in range(5):
            dash.add_record(_rec(passed=False, offset_hours=1))
        points = dash.time_series(bucket_size=timedelta(hours=1))
        assert len(points) == 2

    def test_time_series_pass_rate_per_bucket(self) -> None:
        dash = ComplianceDashboard()
        for _ in range(4):
            dash.add_record(_rec(passed=True, offset_hours=0))
        for _ in range(1):
            dash.add_record(_rec(passed=False, offset_hours=0))
        points = dash.time_series(bucket_size=timedelta(hours=1))
        assert len(points) == 1
        assert abs(points[0].pass_rate - 0.8) < 1e-9

    def test_time_series_point_has_required_fields(self) -> None:
        dash = ComplianceDashboard()
        dash.add_record(_rec(passed=True))
        points = dash.time_series()
        assert len(points) == 1
        p = points[0]
        assert isinstance(p, TimeSeriesPoint)
        assert hasattr(p, "timestamp")
        assert hasattr(p, "pass_rate")
        assert hasattr(p, "total_checks")
        assert hasattr(p, "failures")

    def test_empty_dashboard_time_series(self) -> None:
        dash = ComplianceDashboard()
        assert dash.time_series() == []


# ─────────────────────────────────────────────────────────────────────────────
# Per-verifier stats
# ─────────────────────────────────────────────────────────────────────────────


class TestVerifierStats:
    def test_per_verifier_stats_keys(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_verifier_stats()
        assert "schema" in stats
        assert "tool_safety" in stats

    def test_verifier_stats_pass_rate(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_verifier_stats()
        # schema: 7 passed 1 failed = 87.5%
        schema_stats = stats["schema"]
        assert abs(schema_stats.pass_rate - 7 / 8) < 1e-9

    def test_verifier_stats_has_required_fields(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_verifier_stats()
        s = stats["schema"]
        assert isinstance(s, VerifierStats)
        assert hasattr(s, "verifier_id")
        assert hasattr(s, "total")
        assert hasattr(s, "passed")
        assert hasattr(s, "failed")
        assert hasattr(s, "pass_rate")
        assert hasattr(s, "avg_response_time_ms")

    def test_verifier_stats_failure_count(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_verifier_stats()
        assert stats["schema"].failed == 1
        assert stats["tool_safety"].failed == 1


# ─────────────────────────────────────────────────────────────────────────────
# Per-agent stats
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentStats:
    def test_per_agent_stats_keys(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_agent_stats()
        assert "agent-A" in stats
        assert "agent-B" in stats

    def test_agent_stats_has_required_fields(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_agent_stats()
        s = stats["agent-A"]
        assert isinstance(s, AgentStats)
        assert hasattr(s, "agent_id")
        assert hasattr(s, "total_tasks")
        assert hasattr(s, "passed")
        assert hasattr(s, "failed")
        assert hasattr(s, "pass_rate")

    def test_agent_stats_pass_rate(self, dashboard_with_records: ComplianceDashboard) -> None:
        stats = dashboard_with_records.per_agent_stats()
        # agent-A: 7 passed + 1 failed = 87.5%
        assert abs(stats["agent-A"].pass_rate - 7 / 8) < 1e-9

    def test_agent_none_excluded(self) -> None:
        dash = ComplianceDashboard()
        dash.add_record(_rec(agent_id=None))
        stats = dash.per_agent_stats()
        assert None not in stats
        assert len(stats) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Export formats
# ─────────────────────────────────────────────────────────────────────────────


class TestExportFormats:
    def test_export_json_is_parseable(self, dashboard_with_records: ComplianceDashboard) -> None:
        raw = dashboard_with_records.export_json()
        data = json.loads(raw)
        assert "records" in data
        assert "summary" in data

    def test_export_json_record_count(self, dashboard_with_records: ComplianceDashboard) -> None:
        data = json.loads(dashboard_with_records.export_json())
        assert len(data["records"]) == 10

    def test_export_csv_has_header_and_rows(self, dashboard_with_records: ComplianceDashboard) -> None:
        raw = dashboard_with_records.export_csv()
        reader = csv.DictReader(io.StringIO(raw))
        rows = list(reader)
        assert len(rows) == 10
        assert "task_id" in reader.fieldnames  # type: ignore[operator]
        assert "passed" in reader.fieldnames  # type: ignore[operator]

    def test_export_csv_empty_dashboard(self) -> None:
        dash = ComplianceDashboard()
        raw = dash.export_csv()
        reader = csv.DictReader(io.StringIO(raw))
        rows = list(reader)
        assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# Summary report
# ─────────────────────────────────────────────────────────────────────────────


class TestSummaryReport:
    def test_summary_report_is_non_empty_string(self, dashboard_with_records: ComplianceDashboard) -> None:
        report = dashboard_with_records.generate_summary_report()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_summary_report_contains_pass_rate(self, dashboard_with_records: ComplianceDashboard) -> None:
        report = dashboard_with_records.generate_summary_report()
        assert "80" in report or "0.8" in report  # pass rate 80%

    def test_summary_report_contains_verifier_breakdown(
        self, dashboard_with_records: ComplianceDashboard
    ) -> None:
        report = dashboard_with_records.generate_summary_report()
        assert "schema" in report
        assert "tool_safety" in report

    def test_empty_dashboard_summary(self) -> None:
        dash = ComplianceDashboard()
        report = dash.generate_summary_report()
        assert "0" in report  # total records = 0
