"""
Tests for veridian.hooks.builtin.identity_guard — IdentityGuardHook.
TDD: RED phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from veridian.core.exceptions import SecretNotFound, SecretRotationFailed, VeridianConfigError
from veridian.hooks.builtin.identity_guard import IdentityGuardHook
from veridian.secrets.base import SecretsProvider


# ── Fake secrets provider ────────────────────────────────────────────────────


class FakeSecretsProvider(SecretsProvider):
    provider_id: str = "fake"

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}
        self._rotate_check_called = 0

    def get(self, secret_ref: str) -> str:
        if secret_ref not in self._secrets:
            raise SecretNotFound(secret_ref)
        return self._secrets[secret_ref]

    def rotate_check(self) -> None:
        self._rotate_check_called += 1

    def list_refs(self) -> list[str]:
        return list(self._secrets)


class FailingRotateProvider(SecretsProvider):
    provider_id: str = "failing"

    def get(self, secret_ref: str) -> str:
        return "value"

    def rotate_check(self) -> None:
        raise SecretRotationFailed("credentials expired")

    def list_refs(self) -> list[str]:
        return []


# ── Fake events ──────────────────────────────────────────────────────────────


@dataclass
class _FakeBashOutput:
    cmd: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass
class _FakeResult:
    raw_output: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    bash_outputs: list[Any] = field(default_factory=list)
    error: str = ""


@dataclass
class _FakeTask:
    id: str = "t1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeRunStarted:
    run_id: str = "run-001"
    total_tasks: int = 10


@dataclass
class _FakeTaskClaimed:
    event_type: str = "task.claimed"
    run_id: str = "run-001"
    task: Any = None


@dataclass
class _FakeTaskCompleted:
    event_type: str = "task.completed"
    run_id: str = "run-001"
    task: Any = None
    result: Any = None


# ── Construction ─────────────────────────────────────────────────────────────


class TestIdentityGuardConstruction:
    def test_creates_with_provider(self) -> None:
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        assert hook.id == "identity_guard"
        assert hook.priority == 5

    def test_creates_with_custom_secrets(self) -> None:
        provider = FakeSecretsProvider({"api_key": "sk-secret123"})
        hook = IdentityGuardHook(secrets_provider=provider)
        assert hook._secrets_provider is provider


# ── before_task — rotate_check ───────────────────────────────────────────────


class TestBeforeTaskRotateCheck:
    def test_calls_rotate_check_on_every_before_task(self) -> None:
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        hook.before_task(_FakeTaskClaimed(task=_FakeTask()))
        hook.before_task(_FakeTaskClaimed(task=_FakeTask(id="t2")))
        hook.before_task(_FakeTaskClaimed(task=_FakeTask(id="t3")))

        assert provider._rotate_check_called == 3

    def test_rotation_failure_logged_not_propagated(self) -> None:
        """Hook errors are caught by HookRegistry — rotation failures should not crash."""
        provider = FailingRotateProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())
        # IdentityGuard should handle rotation failure gracefully
        # (log warning, don't crash the run)
        hook.before_task(_FakeTaskClaimed(task=_FakeTask()))


# ── after_task — redaction ───────────────────────────────────────────────────


class TestAfterTaskRedaction:
    def test_redacts_known_secret_from_raw_output(self) -> None:
        provider = FakeSecretsProvider({"api_key": "sk-proj-ABCDEFghijklmnopqrstuvwxyz"})
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(
            raw_output="Connected with key sk-proj-ABCDEFghijklmnopqrstuvwxyz to API"
        )
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "sk-proj-ABCDEFghijklmnopqrstuvwxyz" not in result.raw_output
        assert "[REDACTED:" in result.raw_output

    def test_redacts_secret_from_structured_output(self) -> None:
        provider = FakeSecretsProvider({"db_pass": "postgres://user:s3cr3t@host/db"})
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(
            structured={"connection": "postgres://user:s3cr3t@host/db"}
        )
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "s3cr3t" not in str(result.structured)

    def test_redacts_secret_from_bash_output(self) -> None:
        provider = FakeSecretsProvider({"token": "ghp_ABCDEFghijklmnopqrstuvwxyz1234"})
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        bash = _FakeBashOutput(
            cmd="curl -H 'Authorization: Bearer ghp_ABCDEFghijklmnopqrstuvwxyz1234'",
            stdout="ok",
            stderr="",
        )
        result = _FakeResult(bash_outputs=[bash])
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "ghp_ABCDEFghijklmnopqrstuvwxyz1234" not in bash.cmd

    def test_redacts_pattern_based_secrets_without_provider_values(self) -> None:
        """Should detect API key patterns even if not registered in provider."""
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(
            raw_output="Found key: sk-ant-api03-ABCDEFghijklmnopqrstuvwxyz12345"
        )
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "sk-ant-api03" not in result.raw_output
        assert "[REDACTED:" in result.raw_output

    def test_redacts_error_field(self) -> None:
        provider = FakeSecretsProvider({"key": "AKIAIOSFODNN7EXAMPLE"})
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(error="Auth failed with AKIAIOSFODNN7EXAMPLE")
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "AKIAIOSFODNN7EXAMPLE" not in result.error

    def test_no_change_when_no_secrets(self) -> None:
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(raw_output="Hello world, no secrets here")
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert result.raw_output == "Hello world, no secrets here"

    def test_handles_none_result_gracefully(self) -> None:
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=None))  # no crash


# ── Redaction format ────────────────────────────────────────────────────────


class TestRedactionFormat:
    def test_redaction_uses_pattern_name(self) -> None:
        provider = FakeSecretsProvider()
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(
            raw_output="key: sk-proj-ABCDEFghijklmnopqrstuvwxyz"
        )
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "[REDACTED:openai_api_key]" in result.raw_output

    def test_value_based_redaction_uses_secret_ref(self) -> None:
        provider = FakeSecretsProvider({"my_token": "unique_secret_value_12345678"})
        hook = IdentityGuardHook(secrets_provider=provider)
        hook.before_run(_FakeRunStarted())

        result = _FakeResult(raw_output="using unique_secret_value_12345678 for auth")
        hook.after_task(_FakeTaskCompleted(task=_FakeTask(), result=result))
        assert "[REDACTED:my_token]" in result.raw_output
