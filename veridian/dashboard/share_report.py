"""
veridian.dashboard.share_report

Shareable markdown report generation for completed Veridian runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from veridian.core.atomic_io import atomic_write_text
from veridian.core.exceptions import DashboardError
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary
from veridian.observability.tracer import VeridianTracer

__all__ = ["generate_share_report"]

_BADGE_URL = "https://img.shields.io/badge/Verified%20with-Veridian-0f766e?style=for-the-badge"
_GITHUB_URL = "https://github.com/AV-CSE31/veridian"
_PYPI_URL = "https://pypi.org/project/veridian-ai/"


def generate_share_report(
    ledger: TaskLedger,
    summary: RunSummary,
    output_path: Path | None = None,
) -> Path:
    """Build and atomically persist a shareable run report."""
    destination = output_path or ledger.path.parent / f"veridian_share_{summary.run_id}.md"
    trace_file = ledger.path.parent / "veridian_trace.jsonl"
    content = _build_share_report(ledger=ledger, summary=summary)

    try:
        _atomic_write(destination, content)
    except OSError as exc:
        _record_share_event(
            trace_file=trace_file,
            run_id=summary.run_id,
            event_type="share_report_generation_failed",
            attributes={
                "veridian.run.id": summary.run_id,
                "veridian.share.report_path": str(destination),
                "veridian.error": str(exc),
            },
        )
        raise DashboardError(f"Failed to write share report to {destination}: {exc}") from exc

    _record_share_event(
        trace_file=trace_file,
        run_id=summary.run_id,
        event_type="share_report_generated",
        attributes={
            "veridian.run.id": summary.run_id,
            "veridian.share.report_path": str(destination),
            "veridian.share.total_tasks": summary.total_tasks,
            "veridian.share.done_count": summary.done_count,
            "veridian.share.failed_count": summary.failed_count,
            "veridian.share.dry_run": summary.dry_run,
        },
    )
    return destination


def _build_share_report(ledger: TaskLedger, summary: RunSummary) -> str:
    stats = ledger.stats()
    verified_tasks = ledger.list(status="done")[:3]
    failed_tasks = ledger.list(status="failed")[:3]
    terminal_count = int(stats.pct_complete * stats.total)

    status_label = "Dry run preview" if summary.dry_run else "Verified run"
    if not summary.dry_run and summary.failed_count > 0:
        status_label = "Run completed with failures"

    lines = [
        f"[![Verified with Veridian]({_BADGE_URL})]({_GITHUB_URL})",
        "",
        "# Veridian Verified Run",
        "",
        f"**Run ID:** `{summary.run_id}`  ",
        f"**Status:** {status_label}  ",
        f"**Ledger:** `{ledger.path.name}`  ",
        f"**Generated:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## Snapshot",
        "",
        f"- Tasks verified this run: {summary.done_count}/{summary.total_tasks}",
        f"- Tasks failed this run: {summary.failed_count}",
        f"- Dry run: {'Yes' if summary.dry_run else 'No'}",
        f"- Duration: {summary.duration_seconds:.1f}s",
        f"- Ledger completion: {stats.pct_complete:.0%} ({terminal_count}/{stats.total})",
        "",
        "## Current Verified Tasks",
        "",
    ]

    if verified_tasks:
        for task in verified_tasks:
            lines.append(f"- {task.title}")
    else:
        lines.append("- No verified tasks yet.")

    if failed_tasks:
        lines.extend(
            [
                "",
                "## Current Follow-Ups",
                "",
            ]
        )
        for task in failed_tasks:
            lines.append(f"- {task.title}")

    lines.extend(
        [
            "",
            "## Share This",
            "",
            "- Drop this file into a PR, Slack thread, or customer update to show what Veridian verified.",
            "- Install Veridian: `pip install veridian-ai`",
            f"- GitHub: {_GITHUB_URL}",
            f"- PyPI: {_PYPI_URL}",
        ]
    )

    return "\n".join(lines) + "\n"


def _record_share_event(
    *,
    trace_file: Path,
    run_id: str,
    event_type: str,
    attributes: dict[str, object],
) -> None:
    tracer = VeridianTracer(trace_file=trace_file, use_otel=False)
    tracer._run_id = run_id
    tracer.record_event(event_type, attributes)


def _atomic_write(path: Path, content: str) -> None:
    """Thin alias kept for callers that imported it via this module.

    The actual implementation lives in :func:`veridian.core.atomic_io.atomic_write_text`
    after the Phase 6.A consolidation; this file kept the symbol so
    downstream code that did ``from veridian.dashboard.share_report import
    _atomic_write`` keeps working.
    """
    atomic_write_text(path, content)
