from __future__ import annotations

import pytest

from veridian import VerificationError, verified

CONTRACT = {
    "required": ["decision", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "reason": {"type": "string", "minLength": 8},
    },
}


def test_verified_decorator_returns_verified_call_for_valid_output() -> None:
    @verified(verifier_config={"schema": CONTRACT})
    def decide() -> dict[str, str]:
        return {"decision": "ship", "reason": "all checks passed"}

    result = decide()

    assert result.passed is True
    assert result.error is None
    assert result.structured["decision"] == "ship"
    assert result.task.verifier_id == "schema"
    assert result.result.verified is True
    assert result.report.passed is True
    assert result.result.verification_report["report_hash"] == result.report.report_hash


def test_verified_decorator_returns_failure_without_raising_by_default() -> None:
    @verified(verifier_config={"schema": CONTRACT})
    def decide() -> dict[str, str]:
        return {"decision": "ship"}

    result = decide()

    assert result.passed is False
    assert result.error is not None
    assert "reason" in result.error
    assert result.result.verified is False


def test_verified_decorator_can_raise_on_failure() -> None:
    @verified(verifier_config={"schema": CONTRACT}, strict=True)
    def decide() -> dict[str, str]:
        return {"decision": "ship"}

    with pytest.raises(VerificationError, match="reason"):
        decide()


def test_verified_decorator_can_export_jsonl_report(tmp_path) -> None:
    report_path = tmp_path / "reports.jsonl"

    @verified(verifier_config={"schema": CONTRACT}, report_file=report_path)
    def decide() -> dict[str, str]:
        return {"decision": "ship", "reason": "all checks passed"}

    result = decide()

    assert result.passed is True
    lines = report_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert result.report.report_hash in lines[0]
