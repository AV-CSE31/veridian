"""
tests/unit/test_slo.py
───────────────────────
Unit tests for veridian.observability.slo — SLO definitions and evaluation.

Covers:
  - SLODefinition creation with name, metric, target, window, comparison
  - SLOEvaluator.evaluate() with metrics above/below target
  - Multiple SLOs evaluated at once
  - SLOReport shows compliance status
  - Built-in SLO definitions (task_latency, failure_rate, etc.)
"""

from __future__ import annotations

from veridian.observability.slo import (
    BUILTIN_SLOS,
    SLOComparison,
    SLODefinition,
    SLOEvaluator,
    SLOReport,
)

# ─────────────────────────────────────────────────────────────────────────────
# SLOComparison enum
# ─────────────────────────────────────────────────────────────────────────────


class TestSLOComparison:
    def test_less_than_member(self) -> None:
        assert SLOComparison.LESS_THAN.value == "less_than"

    def test_greater_than_member(self) -> None:
        assert SLOComparison.GREATER_THAN.value == "greater_than"

    def test_equal_member(self) -> None:
        assert SLOComparison.EQUAL.value == "equal"


# ─────────────────────────────────────────────────────────────────────────────
# SLODefinition dataclass
# ─────────────────────────────────────────────────────────────────────────────


class TestSLODefinition:
    def test_creation_with_all_fields(self) -> None:
        slo = SLODefinition(
            name="latency_p99",
            metric_name="task_latency_p99",
            target_value=30.0,
            window_seconds=3600,
            comparison=SLOComparison.LESS_THAN,
            description="P99 latency under 30s",
        )
        assert slo.name == "latency_p99"
        assert slo.metric_name == "task_latency_p99"
        assert slo.target_value == 30.0
        assert slo.window_seconds == 3600
        assert slo.comparison == SLOComparison.LESS_THAN
        assert slo.description == "P99 latency under 30s"

    def test_creation_with_greater_than(self) -> None:
        slo = SLODefinition(
            name="uptime",
            metric_name="uptime_ratio",
            target_value=0.99,
            window_seconds=86400,
            comparison=SLOComparison.GREATER_THAN,
            description="Uptime above 99%",
        )
        assert slo.comparison == SLOComparison.GREATER_THAN

    def test_creation_with_equal(self) -> None:
        slo = SLODefinition(
            name="exact_count",
            metric_name="retry_count",
            target_value=0.0,
            window_seconds=300,
            comparison=SLOComparison.EQUAL,
            description="No retries",
        )
        assert slo.comparison == SLOComparison.EQUAL


# ─────────────────────────────────────────────────────────────────────────────
# SLOReport dataclass
# ─────────────────────────────────────────────────────────────────────────────


class TestSLOReport:
    def test_report_fields(self) -> None:
        report = SLOReport(
            slo_name="latency",
            current_value=25.0,
            target=30.0,
            in_compliance=True,
            window_seconds=3600,
            evaluated_at="2026-04-06T12:00:00+00:00",
        )
        assert report.slo_name == "latency"
        assert report.current_value == 25.0
        assert report.target == 30.0
        assert report.in_compliance is True
        assert report.window_seconds == 3600
        assert report.evaluated_at == "2026-04-06T12:00:00+00:00"

    def test_report_out_of_compliance(self) -> None:
        report = SLOReport(
            slo_name="failure_rate",
            current_value=0.1,
            target=0.05,
            in_compliance=False,
            window_seconds=3600,
            evaluated_at="2026-04-06T12:00:00+00:00",
        )
        assert report.in_compliance is False


# ─────────────────────────────────────────────────────────────────────────────
# SLOEvaluator — single SLO evaluation
# ─────────────────────────────────────────────────────────────────────────────


class TestSLOEvaluatorSingle:
    def test_less_than_in_compliance(self) -> None:
        slo = SLODefinition(
            name="latency",
            metric_name="task_latency_p99",
            target_value=30.0,
            window_seconds=3600,
            comparison=SLOComparison.LESS_THAN,
            description="P99 latency",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"task_latency_p99": 20.0})
        assert len(reports) == 1
        assert reports[0].in_compliance is True
        assert reports[0].current_value == 20.0

    def test_less_than_out_of_compliance(self) -> None:
        slo = SLODefinition(
            name="latency",
            metric_name="task_latency_p99",
            target_value=30.0,
            window_seconds=3600,
            comparison=SLOComparison.LESS_THAN,
            description="P99 latency",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"task_latency_p99": 35.0})
        assert len(reports) == 1
        assert reports[0].in_compliance is False

    def test_greater_than_in_compliance(self) -> None:
        slo = SLODefinition(
            name="uptime",
            metric_name="uptime_ratio",
            target_value=0.99,
            window_seconds=86400,
            comparison=SLOComparison.GREATER_THAN,
            description="Uptime",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"uptime_ratio": 0.999})
        assert reports[0].in_compliance is True

    def test_greater_than_out_of_compliance(self) -> None:
        slo = SLODefinition(
            name="uptime",
            metric_name="uptime_ratio",
            target_value=0.99,
            window_seconds=86400,
            comparison=SLOComparison.GREATER_THAN,
            description="Uptime",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"uptime_ratio": 0.95})
        assert reports[0].in_compliance is False

    def test_equal_in_compliance(self) -> None:
        slo = SLODefinition(
            name="zero_retries",
            metric_name="retry_count",
            target_value=0.0,
            window_seconds=300,
            comparison=SLOComparison.EQUAL,
            description="No retries",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"retry_count": 0.0})
        assert reports[0].in_compliance is True

    def test_equal_out_of_compliance(self) -> None:
        slo = SLODefinition(
            name="zero_retries",
            metric_name="retry_count",
            target_value=0.0,
            window_seconds=300,
            comparison=SLOComparison.EQUAL,
            description="No retries",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"retry_count": 3.0})
        assert reports[0].in_compliance is False

    def test_missing_metric_skipped(self) -> None:
        slo = SLODefinition(
            name="latency",
            metric_name="task_latency_p99",
            target_value=30.0,
            window_seconds=3600,
            comparison=SLOComparison.LESS_THAN,
            description="P99 latency",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"some_other_metric": 10.0})
        assert len(reports) == 0


# ─────────────────────────────────────────────────────────────────────────────
# SLOEvaluator — multiple SLOs
# ─────────────────────────────────────────────────────────────────────────────


class TestSLOEvaluatorMultiple:
    def test_multiple_slos_all_compliant(self) -> None:
        slos = [
            SLODefinition(
                name="latency",
                metric_name="task_latency_p99",
                target_value=30.0,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="P99 latency",
            ),
            SLODefinition(
                name="failure_rate",
                metric_name="failure_rate",
                target_value=0.05,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="Failure rate",
            ),
        ]
        evaluator = SLOEvaluator(definitions=slos)
        reports = evaluator.evaluate({"task_latency_p99": 20.0, "failure_rate": 0.01})
        assert len(reports) == 2
        assert all(r.in_compliance for r in reports)

    def test_multiple_slos_mixed_compliance(self) -> None:
        slos = [
            SLODefinition(
                name="latency",
                metric_name="task_latency_p99",
                target_value=30.0,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="P99 latency",
            ),
            SLODefinition(
                name="failure_rate",
                metric_name="failure_rate",
                target_value=0.05,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="Failure rate",
            ),
        ]
        evaluator = SLOEvaluator(definitions=slos)
        reports = evaluator.evaluate({"task_latency_p99": 20.0, "failure_rate": 0.1})
        assert len(reports) == 2
        compliant = [r for r in reports if r.in_compliance]
        non_compliant = [r for r in reports if not r.in_compliance]
        assert len(compliant) == 1
        assert len(non_compliant) == 1

    def test_report_contains_correct_slo_names(self) -> None:
        slos = [
            SLODefinition(
                name="latency",
                metric_name="task_latency_p99",
                target_value=30.0,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="P99 latency",
            ),
            SLODefinition(
                name="failure_rate",
                metric_name="failure_rate",
                target_value=0.05,
                window_seconds=3600,
                comparison=SLOComparison.LESS_THAN,
                description="Failure rate",
            ),
        ]
        evaluator = SLOEvaluator(definitions=slos)
        reports = evaluator.evaluate({"task_latency_p99": 20.0, "failure_rate": 0.01})
        names = {r.slo_name for r in reports}
        assert names == {"latency", "failure_rate"}

    def test_report_evaluated_at_is_iso_string(self) -> None:
        slo = SLODefinition(
            name="latency",
            metric_name="task_latency_p99",
            target_value=30.0,
            window_seconds=3600,
            comparison=SLOComparison.LESS_THAN,
            description="P99 latency",
        )
        evaluator = SLOEvaluator(definitions=[slo])
        reports = evaluator.evaluate({"task_latency_p99": 20.0})
        assert len(reports) == 1
        # ISO format check: must contain 'T' and timezone info
        assert "T" in reports[0].evaluated_at


# ─────────────────────────────────────────────────────────────────────────────
# BUILTIN_SLOS
# ─────────────────────────────────────────────────────────────────────────────


class TestBuiltinSLOs:
    def test_builtin_slos_is_non_empty_list(self) -> None:
        assert isinstance(BUILTIN_SLOS, list)
        assert len(BUILTIN_SLOS) >= 5

    def test_all_builtins_are_slo_definitions(self) -> None:
        for slo in BUILTIN_SLOS:
            assert isinstance(slo, SLODefinition)

    def test_task_latency_p99_present(self) -> None:
        names = {slo.name for slo in BUILTIN_SLOS}
        assert "task_latency_p99" in names

    def test_failure_rate_present(self) -> None:
        names = {slo.name for slo in BUILTIN_SLOS}
        assert "failure_rate" in names

    def test_retry_rate_present(self) -> None:
        names = {slo.name for slo in BUILTIN_SLOS}
        assert "retry_rate" in names

    def test_cost_per_task_present(self) -> None:
        names = {slo.name for slo in BUILTIN_SLOS}
        assert "cost_per_task" in names

    def test_approval_lag_present(self) -> None:
        names = {slo.name for slo in BUILTIN_SLOS}
        assert "approval_lag" in names

    def test_task_latency_target(self) -> None:
        latency_slo = next(s for s in BUILTIN_SLOS if s.name == "task_latency_p99")
        assert latency_slo.target_value == 30.0
        assert latency_slo.comparison == SLOComparison.LESS_THAN

    def test_failure_rate_target(self) -> None:
        fr_slo = next(s for s in BUILTIN_SLOS if s.name == "failure_rate")
        assert fr_slo.target_value == 0.05
        assert fr_slo.comparison == SLOComparison.LESS_THAN

    def test_retry_rate_target(self) -> None:
        rr_slo = next(s for s in BUILTIN_SLOS if s.name == "retry_rate")
        assert rr_slo.target_value == 0.2
        assert rr_slo.comparison == SLOComparison.LESS_THAN

    def test_cost_per_task_target(self) -> None:
        cost_slo = next(s for s in BUILTIN_SLOS if s.name == "cost_per_task")
        assert cost_slo.target_value == 1.0
        assert cost_slo.comparison == SLOComparison.LESS_THAN

    def test_approval_lag_target(self) -> None:
        lag_slo = next(s for s in BUILTIN_SLOS if s.name == "approval_lag")
        assert lag_slo.target_value == 300.0
        assert lag_slo.comparison == SLOComparison.LESS_THAN

    def test_evaluator_with_builtins(self) -> None:
        evaluator = SLOEvaluator(definitions=BUILTIN_SLOS)
        metrics = {
            "task_latency_p99": 10.0,
            "failure_rate": 0.01,
            "retry_rate": 0.05,
            "cost_per_task": 0.5,
            "approval_lag": 100.0,
        }
        reports = evaluator.evaluate(metrics)
        assert len(reports) == 5
        assert all(r.in_compliance for r in reports)
