"""
Audit Report Generator — human-readable verification evidence.

Produces a report that an attorney or compliance officer can read:
  - Each citation with its verification status
  - Links to the actual case in CourtListener (for verified citations)
  - Highlighted fabricated citations with explanation
  - Summary statistics

Designed for: legal review, compliance audits, court submissions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from extractors.models import VerificationReport, VerificationStatus


def generate_text_report(report: VerificationReport) -> str:
    """Generate a human-readable text audit report."""
    lines: list[str] = []
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"{'=' * 75}")
    lines.append("  CITATION VERIFICATION AUDIT REPORT")
    lines.append(f"  Document: {report.document_id}")
    lines.append(f"  Generated: {ts}")
    lines.append(f"{'=' * 75}")
    lines.append("")

    # Summary
    lines.append("  SUMMARY")
    lines.append(f"  {'-' * 71}")
    lines.append(f"  Total citations found:    {report.total_citations}")
    lines.append(f"  Verified:                 {report.verified}")
    lines.append(f"  Hallucinated (citation):  {report.hallucinated_citations}")
    lines.append(f"  Hallucinated (name):      {report.hallucinated_names}")
    lines.append(f"  Partial matches:          {report.partial_matches}")
    lines.append(f"  Unresolvable:             {report.unresolvable}")
    lines.append(f"  VERDICT:                  {'PASS' if report.passed else 'FAIL — DO NOT FILE'}")
    lines.append("")

    # Detail per citation
    if report.results:
        lines.append("  CITATION DETAIL")
        lines.append(f"  {'-' * 71}")

        for i, r in enumerate(report.results, 1):
            status_icon = {
                VerificationStatus.VERIFIED: "VERIFIED",
                VerificationStatus.HALLUCINATED_CITATION: "!! HALLUCINATED",
                VerificationStatus.HALLUCINATED_NAME: "!! WRONG NAME",
                VerificationStatus.PARTIAL_MATCH: "~  PARTIAL",
                VerificationStatus.UNRESOLVABLE: "?  UNRESOLVABLE",
                VerificationStatus.NOT_CHECKED: "   NOT CHECKED",
            }.get(r.status, "?")

            lines.append(f"  [{i}] {status_icon}")
            lines.append(f"      Citation:  {r.extracted.citation_key}")
            if r.extracted.party_names:
                lines.append(f"      Claimed:   {r.extracted.party_names}")
            if r.resolved_case_name:
                lines.append(f"      Actual:    {r.resolved_case_name}")
            if r.resolved_url:
                lines.append(f"      Source:    {r.resolved_url}")
            if r.name_similarity > 0:
                lines.append(f"      Similarity: {r.name_similarity:.1%}")
            lines.append(f"      Reason:    {r.reason}")
            lines.append("")

    lines.append(f"{'=' * 75}")
    return "\n".join(lines)


def generate_json_report(report: VerificationReport) -> str:
    """Generate a machine-readable JSON audit report."""
    return json.dumps(report.to_dict(), indent=2)
