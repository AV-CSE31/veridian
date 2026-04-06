"""
Local Corpus Resolver — offline fallback for citation verification.

Used when:
  1. CourtListener API is unreachable
  2. Running in air-gapped environments
  3. Testing without network access

The local corpus is a JSON file mapping citation keys to case metadata.
Populate it from CourtListener exports or manual curation.
"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path

from extractors.models import ExtractedCitation, ResolvedCitation, VerificationStatus

log = logging.getLogger(__name__)


# Built-in corpus of landmark US cases — shipped with the example.
# In production, this would be a much larger database.
BUILTIN_CORPUS: dict[str, dict[str, str]] = {
    "347 U.S. 483": {"name": "Brown v. Board of Education", "court": "SCOTUS", "year": "1954"},
    "384 U.S. 436": {"name": "Miranda v. Arizona", "court": "SCOTUS", "year": "1966"},
    "5 U.S. 137": {"name": "Marbury v. Madison", "court": "SCOTUS", "year": "1803"},
    "410 U.S. 113": {"name": "Roe v. Wade", "court": "SCOTUS", "year": "1973"},
    "392 U.S. 1": {"name": "Terry v. Ohio", "court": "SCOTUS", "year": "1968"},
    "367 U.S. 643": {"name": "Mapp v. Ohio", "court": "SCOTUS", "year": "1961"},
    "372 U.S. 335": {"name": "Gideon v. Wainwright", "court": "SCOTUS", "year": "1963"},
    "393 U.S. 503": {"name": "Tinker v. Des Moines", "court": "SCOTUS", "year": "1969"},
    "376 U.S. 254": {"name": "New York Times Co. v. Sullivan", "court": "SCOTUS", "year": "1964"},
    "370 U.S. 421": {"name": "Engel v. Vitale", "court": "SCOTUS", "year": "1962"},
    "381 U.S. 479": {"name": "Griswold v. Connecticut", "court": "SCOTUS", "year": "1965"},
    "388 U.S. 1": {"name": "Loving v. Virginia", "court": "SCOTUS", "year": "1967"},
    "491 U.S. 397": {"name": "Texas v. Johnson", "court": "SCOTUS", "year": "1989"},
    "163 U.S. 537": {"name": "Plessy v. Ferguson", "court": "SCOTUS", "year": "1896"},
    "60 U.S. 393": {"name": "Dred Scott v. Sandford", "court": "SCOTUS", "year": "1857"},
    "418 U.S. 683": {"name": "United States v. Nixon", "court": "SCOTUS", "year": "1974"},
    "558 U.S. 310": {"name": "Citizens United v. FEC", "court": "SCOTUS", "year": "2010"},
    "576 U.S. 644": {"name": "Obergefell v. Hodges", "court": "SCOTUS", "year": "2015"},
    "597 U.S. 215": {
        "name": "Dobbs v. Jackson Women's Health Organization",
        "court": "SCOTUS",
        "year": "2022",
    },
    "573 U.S. 682": {"name": "Burwell v. Hobby Lobby Stores", "court": "SCOTUS", "year": "2014"},
}


class LocalCorpusResolver:
    """Resolves citations against a local JSON corpus."""

    def __init__(self, corpus: dict[str, dict[str, str]] | None = None) -> None:
        self._corpus = corpus or BUILTIN_CORPUS

    @classmethod
    def from_file(cls, path: Path) -> LocalCorpusResolver:
        """Load corpus from a JSON file."""
        data: dict[str, dict[str, str]] = json.loads(path.read_text())
        return cls(corpus=data)

    def resolve(self, citation: ExtractedCitation) -> ResolvedCitation:
        """Resolve against local corpus."""
        key = citation.citation_key
        entry = self._corpus.get(key)

        if not entry:
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.HALLUCINATED_CITATION,
                source="local_corpus",
                reason=f"Citation '{key}' not found in local corpus ({len(self._corpus)} entries)",
            )

        actual_name = entry.get("name", "")
        claimed_name = citation.party_names

        if not claimed_name:
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.VERIFIED,
                resolved_case_name=actual_name,
                resolved_court=entry.get("court", ""),
                resolved_date=entry.get("year", ""),
                source="local_corpus",
                reason="Citation address verified in local corpus",
            )

        similarity = SequenceMatcher(None, claimed_name.lower(), actual_name.lower()).ratio()

        if similarity >= 0.50:
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.VERIFIED,
                resolved_case_name=actual_name,
                resolved_court=entry.get("court", ""),
                resolved_date=entry.get("year", ""),
                source="local_corpus",
                name_similarity=similarity,
                reason=f"Verified against local corpus (similarity={similarity:.2f})",
            )

        return ResolvedCitation(
            extracted=citation,
            status=VerificationStatus.HALLUCINATED_NAME,
            resolved_case_name=actual_name,
            resolved_court=entry.get("court", ""),
            source="local_corpus",
            name_similarity=similarity,
            reason=(
                f"Citation exists as '{actual_name}' but LLM claimed "
                f"'{claimed_name}' (similarity={similarity:.2f})"
            ),
        )
