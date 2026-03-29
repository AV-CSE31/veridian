"""
tests.unit.test_eu_ai_act
──────────────────────────
EU AI Act Compliance Pack — maps EU AI Act articles to verifier configurations,
ComplianceChecker, compliance report generation.
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import ComplianceError, ComplianceGapError
from veridian.compliance.models import (
    ArticleMapping,
    ComplianceReport,
    ComplianceStatus,
    EUAIActArticle,
)
from veridian.compliance.eu_ai_act import (
    EUAIActCompliancePack,
    ComplianceChecker,
)


# ── EUAIActArticle ────────────────────────────────────────────────────────────


class TestEUAIActArticle:
    def test_all_required_articles_exist(self) -> None:
        """The five mandated articles must be defined."""
        required = {
            EUAIActArticle.ARTICLE_9,
            EUAIActArticle.ARTICLE_10,
            EUAIActArticle.ARTICLE_13,
            EUAIActArticle.ARTICLE_14,
            EUAIActArticle.ARTICLE_15,
        }
        assert required.issubset(set(EUAIActArticle))

    def test_articles_have_titles(self) -> None:
        for article in EUAIActArticle:
            assert article.title
            assert len(article.title) > 3

    def test_articles_have_descriptions(self) -> None:
        for article in EUAIActArticle:
            assert article.description
            assert len(article.description) > 10


# ── ArticleMapping ────────────────────────────────────────────────────────────


class TestArticleMapping:
    def test_construct(self) -> None:
        mapping = ArticleMapping(
            article=EUAIActArticle.ARTICLE_9,
            verifier_ids=["tool_safety"],
            notes="Risk management via tool safety analysis",
        )
        assert mapping.article == EUAIActArticle.ARTICLE_9
        assert "tool_safety" in mapping.verifier_ids

    def test_serialise_round_trip(self) -> None:
        mapping = ArticleMapping(
            article=EUAIActArticle.ARTICLE_10,
            verifier_ids=["memory_integrity"],
            notes="Data governance",
        )
        d = mapping.to_dict()
        mapping2 = ArticleMapping.from_dict(d)
        assert mapping2.article == mapping.article
        assert mapping2.verifier_ids == mapping.verifier_ids


# ── EUAIActCompliancePack ─────────────────────────────────────────────────────


class TestEUAIActCompliancePack:
    def test_all_five_articles_mapped(self) -> None:
        pack = EUAIActCompliancePack()
        mapped_articles = {m.article for m in pack.mappings}
        required = {
            EUAIActArticle.ARTICLE_9,
            EUAIActArticle.ARTICLE_10,
            EUAIActArticle.ARTICLE_13,
            EUAIActArticle.ARTICLE_14,
            EUAIActArticle.ARTICLE_15,
        }
        assert required.issubset(mapped_articles)

    def test_article_9_maps_tool_safety(self) -> None:
        pack = EUAIActCompliancePack()
        a9 = pack.get_mapping(EUAIActArticle.ARTICLE_9)
        assert "tool_safety" in a9.verifier_ids

    def test_article_10_maps_memory_integrity(self) -> None:
        pack = EUAIActCompliancePack()
        a10 = pack.get_mapping(EUAIActArticle.ARTICLE_10)
        assert "memory_integrity" in a10.verifier_ids

    def test_article_13_maps_explain(self) -> None:
        """Article 13 (Transparency) → Verification Explanation Engine."""
        pack = EUAIActCompliancePack()
        a13 = pack.get_mapping(EUAIActArticle.ARTICLE_13)
        # Should reference some explanation/transparency verifier or note
        assert a13.verifier_ids or a13.notes

    def test_article_14_maps_human_oversight(self) -> None:
        pack = EUAIActCompliancePack()
        a14 = pack.get_mapping(EUAIActArticle.ARTICLE_14)
        assert a14.verifier_ids or a14.notes

    def test_article_15_maps_consensus(self) -> None:
        pack = EUAIActCompliancePack()
        a15 = pack.get_mapping(EUAIActArticle.ARTICLE_15)
        assert a15.verifier_ids or a15.notes

    def test_get_mapping_unknown_article_raises(self) -> None:
        pack = EUAIActCompliancePack()
        # Create a fake article value not in the pack
        with pytest.raises(ComplianceError, match="not found"):
            pack.get_mapping("nonexistent_article")  # type: ignore[arg-type]

    def test_required_verifier_ids(self) -> None:
        pack = EUAIActCompliancePack()
        all_verifier_ids = pack.required_verifier_ids()
        assert isinstance(all_verifier_ids, set)
        assert "tool_safety" in all_verifier_ids
        assert "memory_integrity" in all_verifier_ids


# ── ComplianceReport ──────────────────────────────────────────────────────────


class TestComplianceReport:
    def test_coverage_100_percent(self) -> None:
        report = ComplianceReport(
            covered=[
                EUAIActArticle.ARTICLE_9,
                EUAIActArticle.ARTICLE_10,
                EUAIActArticle.ARTICLE_13,
                EUAIActArticle.ARTICLE_14,
                EUAIActArticle.ARTICLE_15,
            ],
            gaps=[],
        )
        assert report.coverage_pct == 100.0

    def test_coverage_0_percent(self) -> None:
        report = ComplianceReport(
            covered=[],
            gaps=[
                EUAIActArticle.ARTICLE_9,
                EUAIActArticle.ARTICLE_10,
            ],
        )
        assert report.coverage_pct == 0.0

    def test_coverage_partial(self) -> None:
        report = ComplianceReport(
            covered=[EUAIActArticle.ARTICLE_9, EUAIActArticle.ARTICLE_10],
            gaps=[EUAIActArticle.ARTICLE_13],
        )
        assert abs(report.coverage_pct - 66.67) < 0.1

    def test_is_fully_compliant_true(self) -> None:
        report = ComplianceReport(
            covered=[
                EUAIActArticle.ARTICLE_9,
                EUAIActArticle.ARTICLE_10,
                EUAIActArticle.ARTICLE_13,
                EUAIActArticle.ARTICLE_14,
                EUAIActArticle.ARTICLE_15,
            ],
            gaps=[],
        )
        assert report.is_fully_compliant is True

    def test_is_fully_compliant_false(self) -> None:
        report = ComplianceReport(
            covered=[EUAIActArticle.ARTICLE_9],
            gaps=[EUAIActArticle.ARTICLE_10],
        )
        assert report.is_fully_compliant is False

    def test_serialise_round_trip(self) -> None:
        report = ComplianceReport(
            covered=[EUAIActArticle.ARTICLE_9],
            gaps=[EUAIActArticle.ARTICLE_10, EUAIActArticle.ARTICLE_13],
        )
        d = report.to_dict()
        report2 = ComplianceReport.from_dict(d)
        assert len(report2.covered) == 1
        assert len(report2.gaps) == 2
        assert EUAIActArticle.ARTICLE_9 in report2.covered

    def test_generate_text_report(self) -> None:
        report = ComplianceReport(
            covered=[EUAIActArticle.ARTICLE_9],
            gaps=[EUAIActArticle.ARTICLE_10],
        )
        text = report.to_text_report()
        assert isinstance(text, str)
        assert "Article" in text or "article" in text.lower()
        assert len(text) > 50


# ── ComplianceChecker ─────────────────────────────────────────────────────────


class TestComplianceChecker:
    def test_fully_covered_system(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        all_verifiers = pack.required_verifier_ids()
        report = checker.check(active_verifier_ids=all_verifiers)
        assert report.is_fully_compliant is True
        assert report.gaps == []

    def test_empty_verifier_set_all_gaps(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        report = checker.check(active_verifier_ids=set())
        assert not report.is_fully_compliant
        assert len(report.gaps) > 0

    def test_partial_coverage(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        # Only cover Article 9 verifiers
        a9_verifiers = set(pack.get_mapping(EUAIActArticle.ARTICLE_9).verifier_ids)
        report = checker.check(active_verifier_ids=a9_verifiers)
        assert EUAIActArticle.ARTICLE_9 in report.covered
        # Other articles not covered
        assert len(report.gaps) >= 1

    def test_check_returns_compliance_report(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        report = checker.check(active_verifier_ids={"tool_safety"})
        assert isinstance(report, ComplianceReport)

    def test_suggest_verifiers_for_gap(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        report = checker.check(active_verifier_ids=set())
        suggestions = checker.suggest_verifiers(report)
        assert isinstance(suggestions, dict)
        # Should have suggestions for each gap article
        for article in report.gaps:
            assert article in suggestions or str(article) in str(suggestions)

    def test_check_with_compliance_gap_error_mode(self) -> None:
        """ComplianceChecker can optionally raise ComplianceGapError when gaps exist."""
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        with pytest.raises(ComplianceGapError):
            checker.check(active_verifier_ids=set(), raise_on_gaps=True)

    def test_check_no_error_when_compliant(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        all_verifiers = pack.required_verifier_ids()
        # Should not raise even with raise_on_gaps=True
        report = checker.check(active_verifier_ids=all_verifiers, raise_on_gaps=True)
        assert report.is_fully_compliant is True

    def test_article_status_covered(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        a9_verifiers = set(pack.get_mapping(EUAIActArticle.ARTICLE_9).verifier_ids)
        report = checker.check(active_verifier_ids=a9_verifiers)
        status = report.article_status(EUAIActArticle.ARTICLE_9)
        assert status == ComplianceStatus.COVERED

    def test_article_status_gap(self) -> None:
        pack = EUAIActCompliancePack()
        checker = ComplianceChecker(pack)
        report = checker.check(active_verifier_ids=set())
        status = report.article_status(EUAIActArticle.ARTICLE_9)
        assert status == ComplianceStatus.GAP
