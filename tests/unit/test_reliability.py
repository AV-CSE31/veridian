"""Tests for the Reliability Benchmark suite."""

from __future__ import annotations

from veridian.eval.reliability import ReliabilityBenchmark


class TestReliabilityBenchmark:
    def test_runs_all_four_dimensions(self) -> None:
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        assert len(report.dimensions) == 4
        dims = {d.dimension for d in report.dimensions}
        assert dims == {"Consistency", "Robustness", "Predictability", "Safety"}

    def test_overall_score_is_average(self) -> None:
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        expected = sum(d.score for d in report.dimensions) / 4
        assert abs(report.overall_score - expected) < 0.001

    def test_consistency_is_perfect(self) -> None:
        """Deterministic verifiers must produce identical results every time."""
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        cons = next(d for d in report.dimensions if d.dimension == "Consistency")
        assert cons.score == 1.0

    def test_safety_blocks_all_unsafe(self) -> None:
        """Zero false negatives on known unsafe patterns."""
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        safety = next(d for d in report.dimensions if d.dimension == "Safety")
        assert safety.score == 1.0, (
            f"Safety missed {safety.tests_run - safety.tests_passed} patterns"
        )

    def test_predictability_under_10ms(self) -> None:
        """All verifier calls complete under 10ms."""
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        pred = next(d for d in report.dimensions if d.dimension == "Predictability")
        assert pred.score >= 0.95  # at least 95% under 10ms

    def test_report_to_markdown(self) -> None:
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        md = report.to_markdown()
        assert "Consistency" in md
        assert "Safety" in md
        assert "Overall Score" in md

    def test_report_to_dict(self) -> None:
        benchmark = ReliabilityBenchmark()
        report = benchmark.run()
        d = report.to_dict()
        assert "overall_score" in d
        assert "dimensions" in d
        assert len(d["dimensions"]) == 4
