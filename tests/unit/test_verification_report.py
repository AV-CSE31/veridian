from __future__ import annotations

import json

from veridian.core.report import (
    SCHEMA_VERSION,
    VerificationReport,
    append_report_jsonl,
    validate_report_chain,
)
from veridian.core.task import Task, TaskResult


def _report(passed: bool = True) -> VerificationReport:
    task = Task(
        id="release-1",
        title="Release gate",
        description="Decide whether the release can ship.",
        verifier_id="schema",
        verifier_config={"required": ["decision"]},
    )
    result = TaskResult(
        raw_output='{"decision": "ship"}',
        structured={"decision": "ship"},
    )
    return VerificationReport.from_task_result(
        task=task,
        result=result,
        passed=passed,
        error=None if passed else "missing field",
        evidence={"required": ["decision"]},
        score=1.0 if passed else 0.0,
        runtime_version="0.test",
        run_id="run-1",
        metadata={"source": "test"},
    )


def test_verification_report_has_stable_enterprise_schema() -> None:
    report = _report()
    payload = report.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["task_id"] == "release-1"
    assert payload["verifier_id"] == "schema"
    assert payload["passed"] is True
    assert payload["input_hash"]
    assert payload["output_hash"]
    assert payload["report_hash"] == report.compute_hash()


def test_report_round_trips_from_dict() -> None:
    report = _report()

    restored = VerificationReport.from_dict(report.to_dict())

    assert restored == report
    assert restored.compute_hash() == report.report_hash


def test_jsonl_evidence_chain_links_reports_and_detects_tampering(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"
    first = append_report_jsonl(report_path, _report())
    second = append_report_jsonl(report_path, _report(passed=False))

    assert first.previous_hash is None
    assert second.previous_hash == first.report_hash
    validation = validate_report_chain(report_path)
    assert validation.valid is True
    assert validation.checked_count == 2

    lines = report_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["passed"] = False
    lines[0] = json.dumps(tampered)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    validation = validate_report_chain(report_path)
    assert validation.valid is False
    assert "hash mismatch" in (validation.error or "")
