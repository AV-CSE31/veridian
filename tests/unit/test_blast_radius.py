"""
Tests for veridian.skills.blast_radius — Contamination tracing.
TDD: RED phase.
"""

from __future__ import annotations

import pytest

from veridian.skills.blast_radius import BlastRadiusAnalyzer, ImpactReport


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_task_provenance() -> dict[str, dict[str, list[str]]]:
    """task_id -> {skills_used: [...], downstream_tasks: [...]}"""
    return {
        "t1": {"skills_used": ["s-compromised"], "downstream_tasks": ["t2", "t3"]},
        "t2": {"skills_used": ["s-safe"], "downstream_tasks": ["t4"]},
        "t3": {"skills_used": [], "downstream_tasks": []},
        "t4": {"skills_used": ["s-compromised"], "downstream_tasks": ["t5"]},
        "t5": {"skills_used": [], "downstream_tasks": []},
    }


def _make_skill_provenance() -> dict[str, dict[str, str]]:
    """skill_id -> {source_task_id: ..., extracted_from: ...}"""
    return {
        "s-compromised": {"source_task_id": "t0"},
        "s-safe": {"source_task_id": "t-origin"},
        "s-downstream": {"source_task_id": "t2"},  # extracted from t2 which used s-safe
    }


# ── Construction ─────────────────────────────────────────────────────────────


class TestBlastRadiusAnalyzerConstruction:
    def test_creates_analyzer(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        assert analyzer is not None


# ── Analysis ─────────────────────────────────────────────────────────────────


class TestBlastRadiusAnalysis:
    def test_traces_directly_affected_tasks(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        report = analyzer.analyze("s-compromised")
        assert "t1" in report.affected_tasks
        assert "t4" in report.affected_tasks

    def test_traces_downstream_tasks(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        report = analyzer.analyze("s-compromised")
        # t2, t3 are downstream of t1 which used s-compromised
        assert "t2" in report.affected_tasks or "t2" in report.downstream_tasks
        assert "t3" in report.affected_tasks or "t3" in report.downstream_tasks

    def test_identifies_downstream_skills(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        report = analyzer.analyze("s-compromised")
        # s-downstream was extracted from t2 which is downstream of t1
        assert "s-downstream" in report.downstream_skills

    def test_unknown_skill_returns_empty_report(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        report = analyzer.analyze("nonexistent-skill")
        assert len(report.affected_tasks) == 0

    def test_safe_skill_has_limited_blast_radius(self) -> None:
        analyzer = BlastRadiusAnalyzer(
            task_provenance=_make_task_provenance(),
            skill_provenance=_make_skill_provenance(),
        )
        report = analyzer.analyze("s-safe")
        assert "t2" in report.affected_tasks
        assert "t1" not in report.affected_tasks


# ── ImpactReport ─────────────────────────────────────────────────────────────


class TestImpactReport:
    def test_report_to_dict(self) -> None:
        report = ImpactReport(
            compromised_skill_id="s1",
            affected_tasks=["t1", "t2"],
            downstream_tasks=["t3"],
            downstream_skills=["s2"],
            total_impact_scope=4,
        )
        d = report.to_dict()
        assert d["compromised_skill_id"] == "s1"
        assert d["total_impact_scope"] == 4

    def test_report_to_markdown(self) -> None:
        report = ImpactReport(
            compromised_skill_id="s1",
            affected_tasks=["t1"],
            downstream_tasks=["t2"],
            downstream_skills=["s2"],
            total_impact_scope=3,
        )
        md = report.to_markdown()
        assert "s1" in md
        assert "t1" in md

    def test_empty_report_shows_no_impact(self) -> None:
        report = ImpactReport(compromised_skill_id="s-clean")
        assert report.total_impact_scope == 0
        md = report.to_markdown()
        assert "no impact" in md.lower() or "0" in md
