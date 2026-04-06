"""Tests for UniversalVerifier — cross-framework integration."""

from __future__ import annotations

import pytest

from veridian.integrations.universal import UniversalVerifier, VerificationGate


class TestUniversalVerifier:
    def test_blocks_unsafe_code(self) -> None:
        uv = UniversalVerifier(verifiers=["tool_safety"])
        result = uv.check(code="eval(user_input)")
        assert not result.passed
        assert "eval" in result.error.lower()

    def test_passes_safe_code(self) -> None:
        uv = UniversalVerifier(verifiers=["tool_safety"])
        result = uv.check(code="import json\njson.loads('{}')")
        assert result.passed

    def test_schema_check(self) -> None:
        uv = UniversalVerifier(
            verifiers=["schema"],
            schema_config={"required_fields": ["answer"]},
        )
        result = uv.check(output={"answer": "yes"})
        assert result.passed

    def test_schema_fails_missing_fields(self) -> None:
        uv = UniversalVerifier(
            verifiers=["schema"],
            schema_config={"required_fields": ["answer"]},
        )
        result = uv.check(output={"wrong": "field"})
        assert not result.passed

    def test_multi_verifier_chain(self) -> None:
        uv = UniversalVerifier(
            verifiers=["tool_safety", "schema"],
            schema_config={"required_fields": ["status"]},
        )
        result = uv.check(code="x = 1", output={"status": "ok"})
        assert result.passed
        assert len(result.details) == 2

    def test_includes_timing(self) -> None:
        uv = UniversalVerifier(verifiers=["tool_safety"])
        result = uv.check(code="x = 1")
        assert result.elapsed_ms >= 0

    def test_unknown_verifier_fails_closed_by_default(self) -> None:
        uv = UniversalVerifier(verifiers=["does_not_exist"])
        result = uv.check(output={"status": "ok"})
        assert not result.passed
        assert "unknown verifier" in result.error.lower()

    def test_unknown_verifier_can_be_skipped_in_permissive_mode(self) -> None:
        uv = UniversalVerifier(
            verifiers=["does_not_exist", "schema"],
            schema_config={"required_fields": ["answer"]},
            allow_unknown_verifiers=True,
        )
        result = uv.check(output={"answer": "ok"})
        assert result.passed

    def test_no_active_verifiers_fails_closed(self) -> None:
        uv = UniversalVerifier(verifiers=[], allow_unknown_verifiers=True)
        result = uv.check(output={"status": "ok"})
        assert not result.passed
        assert "no active verifiers" in result.error.lower()


class TestVerificationGate:
    def test_passes_valid_output(self) -> None:
        @VerificationGate(verifiers=["schema"], required_fields=["answer"])
        def my_fn() -> dict[str, str]:
            return {"answer": "yes"}

        result = my_fn()
        assert result["answer"] == "yes"

    def test_raises_on_invalid_output(self) -> None:
        @VerificationGate(verifiers=["schema"], required_fields=["answer"])
        def bad_fn() -> dict[str, str]:
            return {"wrong": "field"}

        from veridian.core.exceptions import VerificationError

        with pytest.raises(VerificationError):
            bad_fn()

    def test_no_raise_mode(self) -> None:
        @VerificationGate(verifiers=["schema"], required_fields=["answer"], raise_on_fail=False)
        def bad_fn() -> dict[str, str]:
            return {"wrong": "field"}

        result = bad_fn()  # should not raise
        assert result == {"wrong": "field"}
