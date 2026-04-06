"""
veridian.compliance
────────────────────
EU AI Act Compliance Pack (Moonshot M1).

Maps EU AI Act articles to Veridian verifier configurations and provides
ComplianceChecker to audit which articles are covered.
"""

from veridian.compliance.eu_ai_act import ComplianceChecker, EUAIActCompliancePack
from veridian.compliance.models import (
    ArticleMapping,
    ComplianceReport,
    ComplianceStatus,
    EUAIActArticle,
)

__all__ = [
    "ArticleMapping",
    "ComplianceChecker",
    "ComplianceReport",
    "ComplianceStatus",
    "EUAIActArticle",
    "EUAIActCompliancePack",
]
