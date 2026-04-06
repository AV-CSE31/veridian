"""
Typed data models for the citation verification pipeline.

Every citation flows through: Extraction → Resolution → Verification → Report.
These models carry the citation through each stage with full provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerificationStatus(Enum):
    """Outcome of verifying a single citation."""

    VERIFIED = "verified"  # Citation exists AND party names match
    HALLUCINATED_CITATION = "hallucinated_citation"  # Citation address doesn't exist
    HALLUCINATED_NAME = "hallucinated_name"  # Address exists but wrong party names
    PARTIAL_MATCH = "partial_match"  # Fuzzy match — case exists but details differ
    UNRESOLVABLE = "unresolvable"  # Could not reach the resolution service
    NOT_CHECKED = "not_checked"  # Extraction-only, not yet resolved


@dataclass
class ExtractedCitation:
    """A citation extracted from text by the parser (eyecite or regex fallback).

    Fields:
        matched_text: The raw text matched (e.g., "347 U.S. 483")
        volume: Reporter volume number
        reporter: Reporter abbreviation (e.g., "U.S.", "F.3d", "F. Supp. 2d")
        page: Starting page number
        year: Year if present in parenthetical
        party_names: Party names extracted from surrounding context
                     (e.g., "Brown v. Board of Education")
        position: Character offset in the source text where citation appears
    """

    matched_text: str = ""
    volume: str = ""
    reporter: str = ""
    page: str = ""
    year: str = ""
    party_names: str = ""
    position: int = 0

    @property
    def citation_key(self) -> str:
        """Canonical form: '347 U.S. 483' — used for API lookups."""
        return f"{self.volume} {self.reporter} {self.page}".strip()


@dataclass
class ResolvedCitation:
    """Result of resolving an ExtractedCitation against a legal database.

    Contains both the original extraction AND the resolution result,
    so the full provenance chain is preserved for audit reports.
    """

    extracted: ExtractedCitation
    status: VerificationStatus = VerificationStatus.NOT_CHECKED

    # From the resolution source (CourtListener, Westlaw, etc.)
    resolved_case_name: str = ""
    resolved_court: str = ""
    resolved_date: str = ""
    resolved_url: str = ""
    source: str = ""  # "courtlistener" | "local_corpus" | "offline"

    # Similarity score for fuzzy matching (0.0-1.0)
    name_similarity: float = 0.0

    # Explanation of the verification decision
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "extracted_text": self.extracted.matched_text,
            "citation_key": self.extracted.citation_key,
            "party_names_claimed": self.extracted.party_names,
            "status": self.status.value,
            "resolved_case_name": self.resolved_case_name,
            "resolved_court": self.resolved_court,
            "resolved_date": self.resolved_date,
            "resolved_url": self.resolved_url,
            "source": self.source,
            "name_similarity": round(self.name_similarity, 3),
            "reason": self.reason,
        }


@dataclass
class VerificationReport:
    """Complete verification report for a legal document."""

    document_id: str = ""
    total_citations: int = 0
    verified: int = 0
    hallucinated_citations: int = 0
    hallucinated_names: int = 0
    partial_matches: int = 0
    unresolvable: int = 0
    results: list[ResolvedCitation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Document passes only if zero hallucinated citations."""
        return self.hallucinated_citations == 0 and self.hallucinated_names == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "total_citations": self.total_citations,
            "verified": self.verified,
            "hallucinated_citations": self.hallucinated_citations,
            "hallucinated_names": self.hallucinated_names,
            "partial_matches": self.partial_matches,
            "unresolvable": self.unresolvable,
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
        }
