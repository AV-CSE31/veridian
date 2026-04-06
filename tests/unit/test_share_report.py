"""Tests for Veridian's shareable verification report."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from veridian.core.exceptions import DashboardError
from veridian.dashboard.share_report import generate_share_report
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary


def _seed_ledger(tmp_path: Path) -> Path:
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "tasks": {
                    "done-1": {
                        "id": "done-1",
                        "title": "Verify customer onboarding flow",
                        "description": "Confirm onboarding completes and records evidence.",
                        "status": "done",
                        "verifier_id": "schema",
                        "verifier_config": {"required_fields": ["answer"]},
                        "priority": 75,
                        "phase": "default",
                        "retry_count": 0,
                        "max_retries": 3,
                        "depends_on": [],
                    },
                    "done-2": {
                        "id": "done-2",
                        "title": "Check policy enforcement output",
                        "description": "Validate the generated policy decision.",
                        "status": "done",
                        "verifier_id": "schema",
                        "verifier_config": {"required_fields": ["answer"]},
                        "priority": 50,
                        "phase": "default",
                        "retry_count": 0,
                        "max_retries": 3,
                        "depends_on": [],
                    },
                    "failed-1": {
                        "id": "failed-1",
                        "title": "Replay failed escalation path",
                        "description": "Reproduce the escalation path failure.",
                        "status": "failed",
                        "verifier_id": "schema",
                        "verifier_config": {"required_fields": ["answer"]},
                        "priority": 40,
                        "phase": "default",
                        "retry_count": 1,
                        "max_retries": 3,
                        "depends_on": [],
                    },
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return ledger_path


class TestGenerateShareReport:
    def test_creates_markdown_with_badge_and_cta(self, tmp_path: Path) -> None:
        ledger_path = _seed_ledger(tmp_path)
        ledger = TaskLedger(path=ledger_path)
        summary = RunSummary(
            run_id="run-share",
            done_count=2,
            failed_count=1,
            total_tasks=3,
            duration_seconds=12.4,
        )

        report_path = generate_share_report(ledger=ledger, summary=summary)

        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "Verified with Veridian" in content
        assert "Verify customer onboarding flow" in content
        assert "pip install veridian-ai" in content
        assert "run-share" in content

    def test_records_share_report_generated_event(self, tmp_path: Path) -> None:
        ledger_path = _seed_ledger(tmp_path)
        ledger = TaskLedger(path=ledger_path)
        summary = RunSummary(
            run_id="run-share",
            done_count=2,
            failed_count=1,
            total_tasks=3,
            duration_seconds=12.4,
        )

        report_path = generate_share_report(ledger=ledger, summary=summary)

        trace_file = tmp_path / "veridian_trace.jsonl"
        events = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]

        generated = next(
            event for event in events if event["event_type"] == "share_report_generated"
        )
        assert generated["run_id"] == "run-share"
        assert generated["attributes"]["veridian.share.report_path"].endswith(report_path.name)

    def test_records_share_report_generation_failed_event(self, tmp_path: Path) -> None:
        ledger_path = _seed_ledger(tmp_path)
        ledger = TaskLedger(path=ledger_path)
        summary = RunSummary(
            run_id="run-share",
            done_count=2,
            failed_count=1,
            total_tasks=3,
            duration_seconds=12.4,
        )

        with (
            patch("veridian.dashboard.share_report.os.replace", side_effect=OSError("disk full")),
            pytest.raises(DashboardError),
        ):
            generate_share_report(ledger=ledger, summary=summary)

        trace_file = tmp_path / "veridian_trace.jsonl"
        events = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
        failed = next(
            event for event in events if event["event_type"] == "share_report_generation_failed"
        )
        assert failed["run_id"] == "run-share"
        assert "disk full" in failed["attributes"]["veridian.error"]
