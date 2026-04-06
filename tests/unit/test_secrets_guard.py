"""
tests.unit.test_secrets_guard
──────────────────────────────
F3 from the 2026-04-06 cleanup audit — "Wire SecretsGuard into built-in
registry + test."

Verifies:
1. SecretsGuard is registered and instantiable via the global verifier registry.
2. Known secret patterns (API keys, passwords, etc.) fail verification.
3. Clean outputs pass verification.
4. Redact mode passes but records redaction evidence.
5. High-entropy string detection works.
"""

from __future__ import annotations

import veridian.verify.builtin  # noqa: F401 — trigger registration
from veridian.core.task import Task, TaskResult
from veridian.verify.base import registry
from veridian.verify.builtin.secrets_guard import SecretsGuard


def _make(raw: str, structured: dict | None = None) -> tuple[Task, TaskResult]:
    task = Task(title="t", verifier_id="secrets_guard")
    result = TaskResult(raw_output=raw, structured=structured or {})
    return task, result


class TestRegistration:
    def test_secrets_guard_is_registered_and_instantiable(self) -> None:
        verifier = registry.get("secrets_guard")
        assert isinstance(verifier, SecretsGuard)


class TestDetection:
    def test_detects_openai_api_key(self) -> None:
        task, result = _make("Here is the key: sk-projXabcdefghijklmnop1234567890")
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed
        assert "Secret" in (vr.error or "")

    def test_detects_anthropic_api_key(self) -> None:
        task, result = _make("sk-ant-api01-ABCDEFGHIJKLMNOP12345678901234567890")
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed

    def test_detects_aws_access_key(self) -> None:
        task, result = _make("AKIAIOSFODNN7EXAMPLE")
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed

    def test_detects_password_field(self) -> None:
        task, result = _make('{"password": "super_s3cr3t_p4ss!"}')
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed

    def test_detects_private_key_header(self) -> None:
        task, result = _make("-----BEGIN RSA PRIVATE KEY-----\nMIIEow...")
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed

    def test_detects_db_connection_string(self) -> None:
        task, result = _make("postgres://admin:password@host:5432/db")
        vr = SecretsGuard().verify(task, result)
        assert not vr.passed


class TestCleanOutput:
    def test_clean_output_passes(self) -> None:
        task, result = _make("The migration is complete. All tests pass.")
        vr = SecretsGuard().verify(task, result)
        assert vr.passed

    def test_empty_output_passes(self) -> None:
        task, result = _make("")
        vr = SecretsGuard().verify(task, result)
        assert vr.passed


class TestRedactMode:
    def test_redact_mode_passes_but_records_evidence(self) -> None:
        task, result = _make("sk-ant-api01-ABCDEFGHIJKLMNOP12345678901234567890")
        vr = SecretsGuard(redact=True).verify(task, result)
        assert vr.passed
        assert vr.evidence.get("count", 0) >= 1
        assert len(vr.evidence.get("redacted", [])) >= 1


class TestEntropyDetection:
    def test_high_entropy_string_is_flagged(self) -> None:
        # 40 chars of mixed-case alphanumeric = high entropy
        token = "aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2yZ"
        task, result = _make(f"The token is {token}")
        vr = SecretsGuard(min_entropy=3.5).verify(task, result)
        assert not vr.passed
        violations = vr.evidence.get("violations", [])
        assert any("entropy" in v.lower() for v in violations)
