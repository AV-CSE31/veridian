"""
veridian.compliance.eu_ai_act
──────────────────────────────
EU AI Act Compliance Pack (Moonshot M1).

Maps the five key EU AI Act articles to Veridian verifier configurations:

  Article 9  (Risk Management)   → ToolSafetyVerifier + risk scoring
  Article 10 (Data Governance)   → MemoryIntegrityVerifier + data provenance
  Article 13 (Transparency)      → Verification Explanation Engine (veridian.explain)
  Article 14 (Human Oversight)   → HumanInTheLoop hook configuration
  Article 15 (Accuracy/Robust.)  → Consensus Verification + adversarial testing

ComplianceChecker: given a set of active verifier IDs, reports which articles
are covered and which are gaps.

Design constraints (CLAUDE.md):
- Raise from the exception hierarchy.
- No mutable module-level state.
"""

from __future__ import annotations

import logging

from veridian.compliance.models import (
    ArticleMapping,
    ComplianceReport,
    EUAIActArticle,
)
from veridian.core.exceptions import ComplianceError, ComplianceGapError

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# EU AI ACT COMPLIANCE PACK
# ─────────────────────────────────────────────────────────────────────────────


class EUAIActCompliancePack:
    """
    Registry of EU AI Act article → Veridian verifier mappings.

    Built-in mappings cover the five articles mandated by the feature spec.
    Additional mappings can be registered via ``add_mapping()``.
    """

    def __init__(self) -> None:
        self._mappings: dict[EUAIActArticle, ArticleMapping] = {}
        self._register_builtin_mappings()

    def _register_builtin_mappings(self) -> None:
        """Register the five mandated article mappings."""

        # Article 9 — Risk Management System
        # → ToolSafetyVerifier catches the most common high-risk tool patterns
        self._mappings[EUAIActArticle.ARTICLE_9] = ArticleMapping(
            article=EUAIActArticle.ARTICLE_9,
            verifier_ids=["tool_safety"],
            notes=(
                "ToolSafetyVerifier provides AST-based static analysis covering "
                "OWASP ASI03 (tool misevolution). Supplement with a risk scoring "
                "hook for full Article 9 coverage."
            ),
        )

        # Article 10 — Data and Data Governance
        # → MemoryIntegrityVerifier validates that memory updates are internally
        #   consistent and non-contradictory against verified ledger facts.
        self._mappings[EUAIActArticle.ARTICLE_10] = ArticleMapping(
            article=EUAIActArticle.ARTICLE_10,
            verifier_ids=["memory_integrity"],
            notes=(
                "MemoryIntegrityVerifier detects reward hacking and temporal bias "
                "in agent memory — key data governance controls for Article 10."
            ),
        )

        # Article 13 — Transparency
        # → The Verification Explanation Engine (veridian.explain) produces
        #   human-readable explanations for every verification decision.
        self._mappings[EUAIActArticle.ARTICLE_13] = ArticleMapping(
            article=EUAIActArticle.ARTICLE_13,
            verifier_ids=["explanation_engine"],
            notes=(
                "veridian.explain.ExplanationEngine provides BRIEF/STANDARD/DETAILED "
                "explanations for every verification decision. Satisfies the transparency "
                "requirement: deployers can interpret system outputs and the reasons "
                "outputs were accepted or rejected."
            ),
        )

        # Article 14 — Human Oversight
        # → HumanReviewHook + HumanInTheLoop configuration
        self._mappings[EUAIActArticle.ARTICLE_14] = ArticleMapping(
            article=EUAIActArticle.ARTICLE_14,
            verifier_ids=["human_review"],
            notes=(
                "HumanReviewHook requires human approval for flagged tasks before "
                "the agent proceeds. Configure human_review_threshold in VeridianConfig "
                "to set the risk score above which human review is mandatory."
            ),
        )

        # Article 15 — Accuracy, Robustness and Cybersecurity
        # → ConsensusVerifier (multi-model agreement) + adversarial testing
        self._mappings[EUAIActArticle.ARTICLE_15] = ArticleMapping(
            article=EUAIActArticle.ARTICLE_15,
            verifier_ids=["schema", "semantic_grounding"],
            notes=(
                "Schema + SemanticGroundingVerifier provide structural and semantic "
                "accuracy checks. For full Article 15 coverage, add ConsensusVerifier "
                "(multi-model agreement) and run adversarial testing via the canary suite."
            ),
        )

    @property
    def mappings(self) -> list[ArticleMapping]:
        """Return all registered mappings."""
        return list(self._mappings.values())

    def get_mapping(self, article: EUAIActArticle) -> ArticleMapping:
        """
        Return the mapping for a given article.

        Raises ComplianceError if the article is not registered.
        """
        if article not in self._mappings:
            raise ComplianceError(
                f"Article '{article}' not found in compliance pack. "
                f"Registered articles: {[a.value for a in self._mappings]}"
            )
        return self._mappings[article]

    def required_verifier_ids(self) -> set[str]:
        """Return the union of all verifier IDs required to achieve full coverage."""
        ids: set[str] = set()
        for mapping in self._mappings.values():
            ids.update(mapping.verifier_ids)
        return ids


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE CHECKER
# ─────────────────────────────────────────────────────────────────────────────


class ComplianceChecker:
    """
    Checks a set of active verifier IDs against the EU AI Act compliance pack
    and produces a ComplianceReport.

    Injected with an EUAIActCompliancePack — swap for a custom pack in tests.
    """

    def __init__(self, pack: EUAIActCompliancePack) -> None:
        self._pack = pack

    def check(
        self,
        active_verifier_ids: set[str],
        raise_on_gaps: bool = False,
    ) -> ComplianceReport:
        """
        Check which articles are covered by the active_verifier_ids set.

        Parameters
        ----------
        active_verifier_ids — set of verifier.id strings currently active
        raise_on_gaps       — if True and any gaps found, raise ComplianceGapError

        Returns a ComplianceReport detailing covered articles and gaps.
        """
        covered: list[EUAIActArticle] = []
        gaps: list[EUAIActArticle] = []

        for mapping in self._pack.mappings:
            # An article is covered if at least one of its required verifiers is active
            if any(vid in active_verifier_ids for vid in mapping.verifier_ids):
                covered.append(mapping.article)
            else:
                gaps.append(mapping.article)

        report = ComplianceReport(covered=covered, gaps=gaps)

        log.info(
            "compliance.check covered=%d gaps=%d pct=%.1f",
            len(covered),
            len(gaps),
            report.coverage_pct,
        )

        if raise_on_gaps and gaps:
            raise ComplianceGapError([a.value for a in gaps])

        return report

    def suggest_verifiers(self, report: ComplianceReport) -> dict[EUAIActArticle, list[str]]:
        """
        For each article in the report's gaps, return the suggested verifier IDs.

        Returns a dict mapping article → list[verifier_id].
        """
        suggestions: dict[EUAIActArticle, list[str]] = {}
        for article in report.gaps:
            try:
                mapping = self._pack.get_mapping(article)
                suggestions[article] = mapping.verifier_ids
            except ComplianceError:
                suggestions[article] = []
        return suggestions
