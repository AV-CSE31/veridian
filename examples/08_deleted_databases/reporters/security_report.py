"""Security report generator for code safety analysis."""

from __future__ import annotations

from datetime import UTC, datetime

from analyzers.models import AnalysisReport


def generate_report(report: AnalysisReport) -> str:
    """Generate human-readable security analysis report."""
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append(f"{'=' * 75}")
    lines.append("  CODE SAFETY ANALYSIS REPORT")
    lines.append(f"  Submission: {report.code_id}")
    lines.append(f"  Generated: {ts}")
    lines.append(f"  Veridian Verdict: {report.veridian_verdict}")
    lines.append(f"{'=' * 75}")
    lines.append("")
    lines.append("  SUMMARY")
    lines.append(f"  {'-' * 71}")
    lines.append(f"  Lines analyzed:      {report.total_lines}")
    lines.append(f"  Threats found:       {len(report.threats)}")
    lines.append(f"  Max threat level:    {report.max_threat_level.value.upper()}")
    lines.append(f"  Decision:            {'BLOCKED' if report.blocked else 'ALLOWED'}")
    lines.append("")

    if report.threats:
        lines.append("  FINDINGS")
        lines.append(f"  {'-' * 71}")
        for i, t in enumerate(report.threats, 1):
            icon = {"critical": "!!!", "high": "!! ", "medium": "!  ", "low": "   "}.get(
                t.level.value, "   "
            )
            lines.append(f"  [{i}] {icon} [{t.level.value.upper():8s}] {t.category.value}")
            lines.append(f"       {t.description}")
            if t.line_number:
                lines.append(f"       Line {t.line_number}: {t.code_snippet[:60]}")
            if t.incident_ref:
                lines.append(f"       Prevents: {t.incident_ref[:70]}")
            lines.append("")

    lines.append(f"{'=' * 75}")
    return "\n".join(lines)
