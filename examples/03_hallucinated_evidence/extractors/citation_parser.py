"""
Citation Extraction Layer — eyecite with regex fallback.

Uses the eyecite library (Free Law Project) as the primary parser.
eyecite is the gold standard for legal citation extraction — it
understands Bluebook format, hundreds of reporter abbreviations,
and short-form references (Id., supra).

Falls back to regex for environments where eyecite isn't installed.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from extractors.models import ExtractedCitation

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Try to import eyecite — fall back to regex if not available
try:
    from eyecite import get_citations as _eyecite_get_citations
    from eyecite.models import FullCaseCitation

    _EYECITE_AVAILABLE = True
except ImportError:
    _EYECITE_AVAILABLE = False


def extract_citations(text: str) -> list[ExtractedCitation]:
    """Extract all legal citations from text.

    Primary: eyecite (Bluebook-aware, hundreds of reporters).
    Fallback: regex (basic "X v. Y, vol Reporter page" patterns).

    Returns structured ExtractedCitation objects with volume, reporter,
    page, and surrounding party names for each citation found.
    """
    if _EYECITE_AVAILABLE:
        return _extract_with_eyecite(text)
    log.warning("eyecite not installed — using regex fallback (less accurate)")
    return _extract_with_regex(text)


def _extract_with_eyecite(text: str) -> list[ExtractedCitation]:
    """Use eyecite to parse citations with full Bluebook awareness."""
    results: list[ExtractedCitation] = []
    raw_cites = _eyecite_get_citations(text)

    for cite in raw_cites:
        if not isinstance(cite, FullCaseCitation):
            continue

        groups = getattr(cite, "groups", {})
        volume = groups.get("volume", "")
        reporter = groups.get("reporter", "")
        page = groups.get("page", "")
        year = groups.get("year", "")

        # Extract party names from context around the citation
        # eyecite gives us the matched text position
        matched = cite.matched_text()
        pos = text.find(matched)
        party_names = _extract_party_names(text, pos)

        results.append(
            ExtractedCitation(
                matched_text=matched,
                volume=str(volume),
                reporter=str(reporter),
                page=str(page),
                year=str(year),
                party_names=party_names,
                position=max(pos, 0),
            )
        )

    log.info(f"eyecite extracted {len(results)} full case citations")
    return results


def _extract_with_regex(text: str) -> list[ExtractedCitation]:
    """Regex fallback — basic volume/reporter/page extraction."""
    # Pattern: "123 F.3d 456" or "347 U.S. 483"
    pattern = re.compile(
        r"(\d{1,4})\s+"
        r"(U\.S\.|S\.\s*Ct\.|L\.\s*Ed\.|F\.\d[a-z]{1,2}|F\.\s*Supp\.(?:\s*\d[a-z]{1,2})?|"
        r"So\.\s*\d[a-z]|N\.E\.\d[a-z]|S\.E\.\d[a-z]|N\.W\.\d[a-z]|S\.W\.\d[a-z]|"
        r"P\.\d[a-z]|A\.\d[a-z]|Cal\.\s*Rptr\.)"
        r"\s+(\d{1,5})"
    )
    results: list[ExtractedCitation] = []

    for match in pattern.finditer(text):
        volume, reporter, page = match.groups()
        party_names = _extract_party_names(text, match.start())

        results.append(
            ExtractedCitation(
                matched_text=match.group(),
                volume=volume,
                reporter=reporter,
                page=page,
                party_names=party_names,
                position=match.start(),
            )
        )

    log.info(f"regex extracted {len(results)} citations")
    return results


def _extract_party_names(text: str, citation_pos: int) -> str:
    """Extract party names from text preceding a citation.

    Looks backwards from the citation position for "X v. Y" pattern.
    Handles common legal writing patterns like:
      "In Brown v. Board of Education, 347 U.S. 483 (1954)..."
      "See Miranda v. Arizona, 384 U.S. 436 (1966)."
    """
    if citation_pos <= 0:
        return ""

    # Look at up to 200 chars before the citation
    window_start = max(0, citation_pos - 200)
    prefix = text[window_start:citation_pos]

    # Find "X v. Y" pattern in the prefix
    # Look for the last occurrence closest to the citation
    v_pattern = re.compile(
        r"([A-Z][a-zA-Z.']+(?:\s+[A-Za-z.']+)*)"
        r"\s+v\.\s+"
        r"([A-Z][a-zA-Z.']+(?:\s+[A-Za-z.']+)*)"
    )
    matches = list(v_pattern.finditer(prefix))
    if not matches:
        return ""

    # Take the last (closest to citation) match
    last = matches[-1]
    plaintiff = last.group(1).strip()
    defendant = last.group(2).strip()

    # Strip common legal prefixes that aren't party names
    strip_words = {"In", "See", "Under", "Per", "Cf", "But", "And", "Also", "While", "As", "From"}
    for word in strip_words:
        if plaintiff.startswith(word + " "):
            plaintiff = plaintiff[len(word) :].strip()
        if plaintiff == word:
            plaintiff = ""

    if plaintiff and defendant:
        return f"{plaintiff} v. {defendant}"
    return ""
