"""
veridian.compliance.models
───────────────────────────
Domain models for the EU AI Act Compliance Pack.

EUAIActArticle   — enum of the five key articles with title/description.
ArticleMapping   — maps one article to one or more verifier IDs.
ComplianceStatus — COVERED or GAP.
ComplianceReport — result of a compliance check run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# EU AI ACT ARTICLE ENUM
# ─────────────────────────────────────────────────────────────────────────────


class EUAIActArticle(Enum):
    """
    Key EU AI Act articles relevant to high-risk AI systems.

    Each member is a string value (the article identifier) with associated
    title and description metadata accessed via the .title / .description
    properties.
    """

    ARTICLE_9 = "article_9"
    ARTICLE_10 = "article_10"
    ARTICLE_13 = "article_13"
    ARTICLE_14 = "article_14"
    ARTICLE_15 = "article_15"

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    def title(self) -> str:
        _titles = {
            "article_9": "Risk Management System",
            "article_10": "Data and Data Governance",
            "article_13": "Transparency and Provision of Information to Deployers",
            "article_14": "Human Oversight",
            "article_15": "Accuracy, Robustness and Cybersecurity",
        }
        return _titles[self.value]

    @property
    def description(self) -> str:
        _descriptions = {
            "article_9": (
                "High-risk AI systems shall implement a risk management system "
                "covering the entire AI system lifecycle, identifying and analysing "
                "known and foreseeable risks."
            ),
            "article_10": (
                "Training, validation and testing data shall meet quality criteria, "
                "be subject to appropriate data governance, and be relevant, "
                "representative and free of errors."
            ),
            "article_13": (
                "High-risk AI systems shall be designed and developed in such a way "
                "that their operation is sufficiently transparent to enable deployers "
                "to interpret and use system outputs appropriately."
            ),
            "article_14": (
                "High-risk AI systems shall be designed and developed in such a way "
                "to allow effective oversight by natural persons during the period "
                "of use of the AI system."
            ),
            "article_15": (
                "High-risk AI systems shall be designed and developed in such a way "
                "that they achieve an appropriate level of accuracy, robustness and "
                "cybersecurity throughout their lifecycle."
            ),
        }
        return _descriptions[self.value]


# ─────────────────────────────────────────────────────────────────────────────
# ARTICLE MAPPING
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ArticleMapping:
    """
    Maps one EU AI Act article to the Veridian verifier IDs that cover it.

    verifier_ids — list of verifier.id strings from the veridian.verifiers registry
    notes        — additional free-text guidance for compliance teams
    """

    article: EUAIActArticle
    verifier_ids: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "article": self.article.value,
            "verifier_ids": self.verifier_ids,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArticleMapping:
        return cls(
            article=EUAIActArticle(d["article"]),
            verifier_ids=d.get("verifier_ids", []),
            notes=d.get("notes", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE STATUS
# ─────────────────────────────────────────────────────────────────────────────


class ComplianceStatus(StrEnum):
    """Coverage status for a single article."""

    COVERED = "covered"
    GAP = "gap"


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ComplianceReport:
    """
    Result of a compliance check run.

    covered — articles satisfied by the active verifier set
    gaps    — articles not satisfied
    """

    covered: list[EUAIActArticle]
    gaps: list[EUAIActArticle]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def coverage_pct(self) -> float:
        total = len(self.covered) + len(self.gaps)
        if total == 0:
            return 0.0
        return len(self.covered) / total * 100.0

    @property
    def is_fully_compliant(self) -> bool:
        return len(self.gaps) == 0

    def article_status(self, article: EUAIActArticle) -> ComplianceStatus:
        if article in self.covered:
            return ComplianceStatus.COVERED
        return ComplianceStatus.GAP

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": [a.value for a in self.covered],
            "gaps": [a.value for a in self.gaps],
            "coverage_pct": self.coverage_pct,
            "is_fully_compliant": self.is_fully_compliant,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComplianceReport:
        return cls(
            covered=[EUAIActArticle(v) for v in d.get("covered", [])],
            gaps=[EUAIActArticle(v) for v in d.get("gaps", [])],
            generated_at=d.get("generated_at", datetime.now(UTC).isoformat()),
        )

    # ── Human-readable report ─────────────────────────────────────────────────

    def to_text_report(self) -> str:
        lines = [
            "EU AI Act Compliance Report",
            "=" * 40,
            f"Generated: {self.generated_at}",
            f"Coverage:  {self.coverage_pct:.1f}%",
            f"Status:    {'COMPLIANT' if self.is_fully_compliant else 'NON-COMPLIANT'}",
            "",
        ]
        if self.covered:
            lines.append("Covered Articles:")
            for article in self.covered:
                lines.append(f"  ✓ {article.value} — {article.title}")
        if self.gaps:
            lines.append("")
            lines.append("Gaps (not covered):")
            for article in self.gaps:
                lines.append(f"  ✗ {article.value} — {article.title}")
                lines.append(f"    {article.description}")
        return "\n".join(lines)
