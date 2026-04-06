"""
tests.unit.test_operator_dlq_triage
──────────────────────────────────────
Unit tests for the DLQ triage view.

Proves:
- Categorize DLQ entries by failure pattern
- Export triage report as dict
- Empty entries handling
"""

from __future__ import annotations

from veridian.operator.dlq_triage import DLQTriageView, FailureCategory

# ── Sample entries ───────────────────────────────────────────────────────────


def _sample_entries() -> list[dict[str, object]]:
    return [
        {"task_id": "t-001", "error": "timeout after 30s", "category": "transient"},
        {"task_id": "t-002", "error": "timeout after 30s", "category": "transient"},
        {"task_id": "t-003", "error": "schema validation failed", "category": "permanent"},
        {"task_id": "t-004", "error": "unknown error xyz", "category": "unknown"},
        {"task_id": "t-005", "error": "timeout after 60s", "category": "transient"},
    ]


# ── Categorize ───────────────────────────────────────────────────────────────


class TestCategorize:
    def test_groups_by_error(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        assert len(categories) >= 2  # at least timeout + schema

    def test_category_has_count(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        for cat in categories:
            assert cat.count > 0

    def test_category_has_sample_ids(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        for cat in categories:
            assert len(cat.sample_task_ids) > 0

    def test_returns_failure_category_list(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        assert all(isinstance(c, FailureCategory) for c in categories)

    def test_empty_entries(self) -> None:
        view = DLQTriageView()
        categories = view.categorize([])
        assert len(categories) == 0


# ── Export report ────────────────────────────────────────────────────────────


class TestExportReport:
    def test_returns_dict(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        report = view.export_report(categories)
        assert isinstance(report, dict)

    def test_report_contains_categories(self) -> None:
        view = DLQTriageView()
        categories = view.categorize(_sample_entries())
        report = view.export_report(categories)
        assert "categories" in report

    def test_report_from_empty(self) -> None:
        view = DLQTriageView()
        report = view.export_report([])
        assert isinstance(report, dict)
        assert report["categories"] == []
