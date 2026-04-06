"""
Citation Verification Pipeline — Enterprise-Grade
===================================================
Multi-layer extraction → resolution → verification → audit report.

Architecture:
  1. EXTRACTION (eyecite):  Parse text → structured citations
  2. RESOLUTION (CourtListener / local corpus):  Verify each citation exists
  3. PARTY NAME CHECK (fuzzy matching):  Verify claimed case names match
  4. VERIDIAN INTEGRATION (BaseVerifier):  Gate agent output
  5. AUDIT REPORT:  Human-readable evidence for compliance

INCIDENT: Mata v. Avianca, Inc. (S.D.N.Y. 2023)
  6 fabricated citations. $5,000 sanction. 100+ subsequent rulings cite this case.

USAGE:
    pip install veridian-ai eyecite httpx
    cd examples/03_hallucinated_evidence

    # Verify the sample brief (contains 4 real + 4 fabricated citations):
    python pipeline.py

    # Verify any text file:
    python pipeline.py path/to/brief.txt

    # Use with Veridian verifier in your pipeline:
    from pipeline import CitationPipelineVerifier
    verifier = CitationPipelineVerifier(mode="local")
    result = verifier.verify(task, task_result)
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import ClassVar

from extractors.citation_parser import extract_citations
from extractors.models import (
    ExtractedCitation,
    ResolvedCitation,
    VerificationReport,
    VerificationStatus,
)
from reporters.audit_report import generate_text_report
from resolvers.courtlistener import CourtListenerResolver
from resolvers.local_corpus import LocalCorpusResolver

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)


class CitationPipelineVerifier(BaseVerifier):
    """Enterprise citation verification — Veridian BaseVerifier integration.

    Modes:
      "api"   — Use CourtListener API (live, most comprehensive)
      "local" — Use built-in corpus (offline, fast, limited)
      "both"  — Try API first, fall back to local corpus

    This is a real BaseVerifier that plugs into VeridianRunner.
    Register via pyproject.toml entry-points for autodiscovery.
    """

    id: ClassVar[str] = "citation_pipeline"
    description: ClassVar[str] = (
        "Enterprise citation verification: eyecite extraction → "
        "CourtListener resolution → party name fuzzy matching"
    )

    def __init__(self, mode: str = "both") -> None:
        self._mode = mode
        self._api_resolver: CourtListenerResolver | None = None
        self._local_resolver = LocalCorpusResolver()

        if mode in ("api", "both"):
            self._api_resolver = CourtListenerResolver()

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run the full citation pipeline on agent output."""
        raw = getattr(result, "raw_output", "") or ""
        if not raw.strip():
            return VerificationResult(passed=True, evidence={"citations_found": 0})

        report = self.run_pipeline(raw, document_id=task.id)

        if report.passed:
            return VerificationResult(
                passed=True,
                evidence=report.to_dict(),
            )

        # Build specific error message
        issues: list[str] = []
        for r in report.results:
            if r.status == VerificationStatus.HALLUCINATED_CITATION:
                issues.append(f"Fabricated: {r.extracted.citation_key}")
            elif r.status == VerificationStatus.HALLUCINATED_NAME:
                issues.append(
                    f"Wrong name: {r.extracted.party_names} at {r.extracted.citation_key} "
                    f"(actual: {r.resolved_case_name})"
                )

        error = "; ".join(issues[:3])
        if len(issues) > 3:
            error += f" (+{len(issues) - 3} more)"

        return VerificationResult(
            passed=False,
            error=error,
            evidence=report.to_dict(),
        )

    def run_pipeline(self, text: str, document_id: str = "doc") -> VerificationReport:
        """Execute the full extraction → resolution → report pipeline."""
        # Step 1: Extract citations
        citations = extract_citations(text)
        log.info(f"Extracted {len(citations)} citations from document '{document_id}'")

        # Step 2: Resolve each citation
        resolved: list[ResolvedCitation] = []
        for cite in citations:
            result = self._resolve_single(cite)
            resolved.append(result)

        # Step 3: Build report
        report = VerificationReport(
            document_id=document_id,
            total_citations=len(resolved),
            verified=sum(1 for r in resolved if r.status == VerificationStatus.VERIFIED),
            hallucinated_citations=sum(
                1 for r in resolved if r.status == VerificationStatus.HALLUCINATED_CITATION
            ),
            hallucinated_names=sum(
                1 for r in resolved if r.status == VerificationStatus.HALLUCINATED_NAME
            ),
            partial_matches=sum(
                1 for r in resolved if r.status == VerificationStatus.PARTIAL_MATCH
            ),
            unresolvable=sum(1 for r in resolved if r.status == VerificationStatus.UNRESOLVABLE),
            results=resolved,
        )

        return report

    def _resolve_single(self, citation: ExtractedCitation) -> ResolvedCitation:
        """Resolve one citation using the configured mode."""
        # Try API first if available
        if self._api_resolver and self._mode in ("api", "both"):
            try:
                result = self._api_resolver.resolve(citation)
                if result.status != VerificationStatus.UNRESOLVABLE:
                    return result
            except Exception as e:
                log.warning(f"API resolution failed for '{citation.citation_key}': {e}")

        # Fall back to local corpus
        if self._mode in ("local", "both"):
            return self._local_resolver.resolve(citation)

        return ResolvedCitation(
            extracted=citation,
            status=VerificationStatus.UNRESOLVABLE,
            reason="No resolver available",
        )


def main() -> None:
    """CLI entry point — verify a legal brief."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    # Determine input
    if len(sys.argv) > 1:
        brief_path = Path(sys.argv[1])
        if not brief_path.exists():
            print(f"File not found: {brief_path}")
            sys.exit(1)
        text = brief_path.read_text()
        doc_id = brief_path.name
    else:
        # Use sample brief
        sample = Path(__file__).parent / "data" / "sample_brief.txt"
        if not sample.exists():
            print("No input file specified and sample_brief.txt not found")
            sys.exit(1)
        text = sample.read_text()
        doc_id = "sample_brief.txt"

    # Run pipeline
    start = time.monotonic()

    # Use local mode by default (no API calls needed for demo)
    # Switch to "api" or "both" for production CourtListener integration
    verifier = CitationPipelineVerifier(mode="local")
    report = verifier.run_pipeline(text, document_id=doc_id)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Print audit report
    print(generate_text_report(report))
    print("  Pipeline: eyecite → local corpus → fuzzy match")
    print("  Mode: local (use --api for CourtListener)")
    print(f"  Elapsed: {elapsed_ms}ms")

    if not report.passed:
        print("\n  !! DOCUMENT CONTAINS FABRICATED CITATIONS — DO NOT FILE !!")
        sys.exit(1)
    else:
        print("\n  All citations verified.")


if __name__ == "__main__":
    main()
