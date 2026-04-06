"""
CourtListener API Resolver — verifies citations against the Free Law Project database.

CourtListener (courtlistener.com) is maintained by the Free Law Project and
contains millions of federal and state opinions. The API is free for
non-commercial use (no API key required for basic lookups).

This resolver:
  1. Takes an ExtractedCitation with volume/reporter/page
  2. Queries CourtListener's search API
  3. Checks if the citation address exists
  4. If it exists, compares the claimed party names against the actual case name
  5. Returns a ResolvedCitation with VERIFIED / HALLUCINATED_CITATION / HALLUCINATED_NAME

Production note: For high-volume use, register for an API key and add
Redis/PostgreSQL caching to avoid rate limits.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

import httpx
from extractors.models import ExtractedCitation, ResolvedCitation, VerificationStatus

log = logging.getLogger(__name__)

COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
DEFAULT_TIMEOUT = 10.0
NAME_SIMILARITY_THRESHOLD = 0.50  # Minimum similarity for party name match


class CourtListenerResolver:
    """Resolves legal citations against the CourtListener database.

    Three-step verification:
      Step 1: Does the citation address (vol/reporter/page) exist?
      Step 2: If yes, do the claimed party names match the real case?
      Step 3: If exact match fails, compute fuzzy similarity score.

    This catches both hallucination types from Mata v. Avianca:
      - Completely fabricated citations (address doesn't exist)
      - Real citation address with fake party names attached
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        similarity_threshold: float = NAME_SIMILARITY_THRESHOLD,
    ) -> None:
        self._timeout = timeout
        self._threshold = similarity_threshold
        self._client = httpx.Client(timeout=self._timeout)
        # Simple in-memory cache to avoid duplicate API calls
        self._cache: dict[str, list[dict[str, object]]] = {}

    def resolve(self, citation: ExtractedCitation) -> ResolvedCitation:
        """Resolve a single citation against CourtListener."""
        key = citation.citation_key
        if not key.strip():
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.UNRESOLVABLE,
                reason="Empty citation key — cannot resolve",
                source="courtlistener",
            )

        # Step 1: Query CourtListener
        try:
            results = self._search(key)
        except Exception as e:
            log.warning(f"CourtListener API error for '{key}': {e}")
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.UNRESOLVABLE,
                reason=f"API error: {e}",
                source="courtlistener",
            )

        # Step 2: Does the citation address exist?
        matching = self._find_exact_citation(key, results)
        if not matching:
            log.info(f"HALLUCINATED: '{key}' not found in CourtListener")
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.HALLUCINATED_CITATION,
                reason=f"Citation '{key}' does not exist in CourtListener database",
                source="courtlistener",
            )

        # Step 3: Check party names
        actual_name = matching.get("caseName", "")
        claimed_name = citation.party_names

        if not claimed_name:
            # No party names to check — citation address verified
            return ResolvedCitation(
                extracted=citation,
                status=VerificationStatus.VERIFIED,
                resolved_case_name=str(actual_name),
                resolved_court=str(matching.get("court", "")),
                resolved_date=str(matching.get("dateFiled", "")),
                resolved_url=f"https://www.courtlistener.com{matching.get('absolute_url', '')}",
                source="courtlistener",
                reason="Citation address verified (no party names to check)",
            )

        # Fuzzy match party names
        similarity = self._name_similarity(claimed_name, str(actual_name))

        if similarity >= self._threshold:
            status = (
                VerificationStatus.VERIFIED
                if similarity > 0.7
                else VerificationStatus.PARTIAL_MATCH
            )
            return ResolvedCitation(
                extracted=citation,
                status=status,
                resolved_case_name=str(actual_name),
                resolved_court=str(matching.get("court", "")),
                resolved_date=str(matching.get("dateFiled", "")),
                resolved_url=f"https://www.courtlistener.com{matching.get('absolute_url', '')}",
                source="courtlistener",
                name_similarity=similarity,
                reason=f"Party names match (similarity={similarity:.2f})",
            )

        # Citation address exists BUT party names don't match
        log.info(
            f"HALLUCINATED NAME: '{key}' exists as '{actual_name}' "
            f"but claimed '{claimed_name}' (similarity={similarity:.2f})"
        )
        return ResolvedCitation(
            extracted=citation,
            status=VerificationStatus.HALLUCINATED_NAME,
            resolved_case_name=str(actual_name),
            resolved_court=str(matching.get("court", "")),
            resolved_date=str(matching.get("dateFiled", "")),
            resolved_url=f"https://www.courtlistener.com{matching.get('absolute_url', '')}",
            source="courtlistener",
            name_similarity=similarity,
            reason=(
                f"Citation address exists as '{actual_name}' but LLM claimed "
                f"'{claimed_name}' (similarity={similarity:.2f} < {self._threshold})"
            ),
        )

    def _search(self, citation_key: str) -> list[dict[str, object]]:
        """Query CourtListener API with caching."""
        if citation_key in self._cache:
            return self._cache[citation_key]

        encoded = citation_key.replace(" ", "+")
        response = self._client.get(
            COURTLISTENER_SEARCH_URL,
            params={"type": "o", "citation": encoded},
        )
        response.raise_for_status()
        results: list[dict[str, object]] = response.json().get("results", [])
        self._cache[citation_key] = results
        return results

    def _find_exact_citation(
        self, citation_key: str, results: list[dict[str, object]]
    ) -> dict[str, object] | None:
        """Find the result whose citation list contains our exact citation."""
        normalized_key = citation_key.lower().replace("  ", " ").strip()
        for result in results:
            citations_list = result.get("citation", [])
            if isinstance(citations_list, list):
                for c in citations_list:
                    if str(c).lower().replace("  ", " ").strip() == normalized_key:
                        return result
        return None

    @staticmethod
    def _name_similarity(claimed: str, actual: str) -> float:
        """Compute similarity between claimed and actual party names.

        Uses SequenceMatcher for fuzzy matching. This handles:
          - Abbreviation differences: "N.F.I.B." vs "Nat'l Fed'n"
          - Ordering: "Board of Education v. Brown" vs "Brown v. Board"
          - Minor spelling variations
        """
        if not claimed or not actual:
            return 0.0
        claimed_lower = claimed.lower().strip()
        actual_lower = actual.lower().strip()

        # Exact match
        if claimed_lower == actual_lower:
            return 1.0

        # Check if claimed is a substring of actual or vice versa
        if claimed_lower in actual_lower or actual_lower in claimed_lower:
            return 0.85

        # SequenceMatcher fuzzy match
        return SequenceMatcher(None, claimed_lower, actual_lower).ratio()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
