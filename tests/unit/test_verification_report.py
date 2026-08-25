from __future__ import annotations

import json
import os

import pytest

from veridian.core.exceptions import VerificationError
from veridian.core.report import (
    SCHEMA_VERSION,
    VerificationReport,
    append_report_jsonl,
    sign_report,
    stable_hash,
    validate_report_chain,
)
from veridian.core.task import Task, TaskResult

SIGNING_KEY = "unit-test-report-key-material-32-bytes"
LEGACY_V1_REPORT = (
    '{"created_at":"2025-01-02T03:04:05+00:00","error":null,'
    '"evidence":{"required":["decision"]},'
    '"input_hash":"1111111111111111111111111111111111111111111111111111111111111111",'
    '"metadata":{"source":"archived"},'
    '"output_hash":"2222222222222222222222222222222222222222222222222222222222222222",'
    '"passed":true,"previous_hash":null,'
    '"report_hash":"a2c0fc5a94f6bfa359a4794871c580c77d2c457d334ea3eff35146f2d9f19e25",'
    '"report_id":"legacy-report-1","run_id":"legacy-run-1",'
    '"runtime_version":"0.7.0","schema_version":"verification-report.v1",'
    '"score":1.0,"task_id":"legacy-task-1","task_title":"Legacy release gate",'
    '"verifier_id":"schema","verifier_version":"0.7.0"}'
)


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


def test_report_commits_to_payloads_without_persisting_them_by_default() -> None:
    report = _report()

    assert report.input_payload == {}
    assert report.output_payload == {}
    assert report.task == {}
    assert report.result == {}
    assert report.task_title == ""
    assert report.evidence == {}
    assert report.metadata == {}
    assert report.payloads_disclosed is False
    assert report.evidence_disclosed is False
    assert report.metadata_disclosed is False
    assert report.evidence_hash == stable_hash({"required": ["decision"]})
    assert report.metadata_hash == stable_hash({"source": "test"})
    assert report.input_hash == stable_hash(
        {
            "id": "release-1",
            "title": "Release gate",
            "description": "Decide whether the release can ship.",
            "phase": "default",
            "verifier_id": "schema",
            "verifier_config": {"required": ["decision"]},
            "depends_on": [],
            "metadata": {},
        }
    )
    assert report.output_hash == stable_hash(
        {
            "raw_output": '{"decision": "ship"}',
            "structured": {"decision": "ship"},
            "artifacts": [],
            "bash_outputs": [],
            "token_usage": {},
            "tool_calls": [],
        }
    )


def test_report_round_trips_from_dict() -> None:
    report = _report()

    restored = VerificationReport.from_dict(report.to_dict())

    assert restored == report
    assert restored.compute_hash() == report.report_hash


def test_jsonl_evidence_chain_links_reports_and_detects_tampering(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"
    first = append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)
    second = append_report_jsonl(report_path, _report(passed=False), signing_key=SIGNING_KEY)

    assert first.previous_hash is None
    assert second.previous_hash == first.report_hash
    validation = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert validation.valid is True
    assert validation.checked_count == 2

    lines = report_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["passed"] = False
    lines[0] = json.dumps(tampered)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    validation = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert validation.valid is False
    assert "hash mismatch" in (validation.error or "")


def test_signing_and_durable_append_require_an_operator_key(tmp_path) -> None:
    with pytest.raises(VerificationError, match="signing key is required"):
        sign_report(_report())
    with pytest.raises(VerificationError, match="signing key is required"):
        append_report_jsonl(tmp_path / "reports.jsonl", _report())
    with pytest.raises(VerificationError, match="at least 32 bytes"):
        sign_report(_report(), signing_key="shared-default")


def test_validation_requires_key_and_reports_external_anchor_status(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"
    written = append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)

    without_key = validate_report_chain(report_path)
    assert without_key.valid is False
    assert "signing key is required" in (without_key.error or "")

    unanchored = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert unanchored.valid is True
    assert unanchored.anchored is False
    assert unanchored.head_hash == written.report_hash
    assert unanchored.limitations

    anchored = validate_report_chain(
        report_path,
        signing_key=SIGNING_KEY,
        trusted_head=written.report_hash,
    )
    assert anchored.valid is True
    assert anchored.anchored is True

    wrong_anchor = validate_report_chain(
        report_path,
        signing_key=SIGNING_KEY,
        trusted_head="0" * 64,
    )
    assert wrong_anchor.valid is False
    assert "trusted head mismatch" in (wrong_anchor.error or "")


def test_append_rejects_malformed_tail_without_changing_existing_bytes(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"
    append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)
    malformed = report_path.read_bytes() + b'{"truncated":'
    report_path.write_bytes(malformed)

    with pytest.raises(VerificationError, match="invalid JSON"):
        append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)

    assert report_path.read_bytes() == malformed


def test_failed_atomic_replace_preserves_last_acknowledged_chain(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "reports.jsonl"
    append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)
    acknowledged = report_path.read_bytes()

    def fail_replace(source, destination) -> None:
        del source, destination
        raise OSError("simulated storage failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(VerificationError, match="durable report write failed"):
        append_report_jsonl(report_path, _report(passed=False), signing_key=SIGNING_KEY)

    assert report_path.read_bytes() == acknowledged
    assert list(tmp_path.glob(".reports.jsonl.*.tmp")) == []


def test_full_chain_validation_rejects_duplicate_keys_and_wrong_types(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"
    append_report_jsonl(report_path, _report(), signing_key=SIGNING_KEY)
    original = report_path.read_text(encoding="utf-8").strip()
    duplicated = original.replace('"passed":true', '"passed":true,"passed":false')
    report_path.write_text(duplicated + "\n", encoding="utf-8")

    duplicate_validation = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert duplicate_validation.valid is False
    assert "duplicate field" in (duplicate_validation.error or "")

    payload = json.loads(original)
    payload["passed"] = "true"
    report_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    type_validation = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert type_validation.valid is False
    assert "passed must be a boolean" in (type_validation.error or "")


def test_legacy_v1_validation_is_read_only_explicit_and_marks_unsigned_evidence(
    tmp_path,
) -> None:
    report_path = tmp_path / "legacy-reports.jsonl"
    report_path.write_text(LEGACY_V1_REPORT + "\n", encoding="utf-8")

    default_validation = validate_report_chain(report_path, signing_key=SIGNING_KEY)
    assert default_validation.valid is False
    assert "allow_legacy_v1=True" in (default_validation.error or "")

    legacy_validation = validate_report_chain(report_path, allow_legacy_v1=True)
    assert legacy_validation.valid is True
    assert legacy_validation.checked_count == 1
    assert legacy_validation.legacy_unsigned_count == 1
    assert any("unsigned" in limitation for limitation in legacy_validation.limitations)

    tampered = json.loads(LEGACY_V1_REPORT)
    tampered["passed"] = False
    report_path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    tampered_validation = validate_report_chain(report_path, allow_legacy_v1=True)
    assert tampered_validation.valid is False
    assert "hash mismatch" in (tampered_validation.error or "")


def test_payload_disclosure_is_explicit_and_self_consistent() -> None:
    task = Task(id="sensitive", title="payment", description="secret account 123")
    result = TaskResult(raw_output="secret token", structured={"token": "secret"})
    report = VerificationReport.from_task_result(
        task=task,
        result=result,
        passed=True,
        error=None,
        evidence={"matched_value": "secret-evidence"},
        score=None,
        runtime_version="test",
        include_payloads=True,
        include_evidence=True,
        include_metadata=True,
        metadata={"customer_reference": "secret-reference"},
    )

    assert report.input_payload["description"] == "secret account 123"
    assert report.output_payload["raw_output"] == "secret token"
    assert report.task == report.input_payload
    assert report.result == report.output_payload
    assert report.evidence == {"matched_value": "secret-evidence"}
    assert report.metadata == {"customer_reference": "secret-reference"}
    assert report.payloads_disclosed is True
    assert report.evidence_disclosed is True
    assert report.metadata_disclosed is True
