"""
tests.unit.test_phase1a_security_guards
---------------------------------------------------------------------------------------------------------------------
Acceptance tests for the Phase 1.A security hardening additions:

* BashExitCodeVerifier scrubs the child-process env so
  parent secrets cannot leak into agent-issued shells.
* LiteLLMProvider rejects URL-shaped model strings and enforces a provider
  prefix allowlist controllable via ``VERIDIAN_ALLOWED_MODELS``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from veridian.core.exceptions import ProviderError, VeridianConfigError
from veridian.providers.litellm_provider import (
    LiteLLMProvider,
    _validate_model_string,
)
from veridian.verify.builtin.bash import BashExitCodeVerifier


def _python_cmd(code: str) -> str:
    """Build a shell-safe Python command for the current platform."""
    args = [sys.executable, "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(arg) for arg in args)


# ------ BashExitCodeVerifier ------------------------------------------------------------------------------------------------------------------------------------------------------------


class TestBashExitCodeVerifierGuards:
    def test_blocklist_rejects_dangerous_command_at_construction(self) -> None:
        with pytest.raises(VeridianConfigError, match="blocklist"):
            BashExitCodeVerifier(command="rm -rf /tmp/anything")

    def test_blocklist_can_be_overridden(self) -> None:
        # An empty blocklist allows previously-blocked patterns through.
        v = BashExitCodeVerifier(command="rm -rf /tmp/anything", blocklist=[])
        assert v.command == "rm -rf /tmp/anything"

    def test_env_scrubbed_by_default(self) -> None:
        from veridian.core.task import Task, TaskResult

        v = BashExitCodeVerifier(
            command=_python_cmd(
                "import os, sys; "
                "sys.exit(1 if os.getenv('OPENAI_API_KEY') "
                "or os.getenv('AWS_SECRET_ACCESS_KEY') else 0)"
            ),
            timeout_seconds=5,
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-secret", "AWS_SECRET_ACCESS_KEY": "leak"},
            clear=False,
        ):
            outcome = v.verify(Task(title="t"), TaskResult(raw_output=""))
        assert outcome.passed  # secrets were stripped, grep finds nothing


# ------ LiteLLM model allowlist ---------------------------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def clean_model_env() -> Iterator[None]:
    """Ensure VERIDIAN_* model env vars don't leak between tests."""
    saved = {
        k: os.environ[k] for k in ("VERIDIAN_MODEL", "VERIDIAN_ALLOWED_MODELS") if k in os.environ
    }
    for k in ("VERIDIAN_MODEL", "VERIDIAN_ALLOWED_MODELS"):
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ[k] = v


class TestModelAllowlist:
    def test_default_provider_prefixes_accepted(self, clean_model_env) -> None:
        # Should not raise:
        _validate_model_string("gemini/gemini-2.5-flash")
        _validate_model_string("claude-opus-4-7")
        _validate_model_string("gpt-4o")
        _validate_model_string("bedrock/anthropic.claude-3-haiku")

    def test_url_shaped_model_rejected(self, clean_model_env) -> None:
        with pytest.raises(ProviderError, match="URL"):
            _validate_model_string("https://attacker.example/v1/messages")

    def test_protocol_relative_rejected(self, clean_model_env) -> None:
        with pytest.raises(ProviderError, match="URL"):
            _validate_model_string("//attacker.example/v1")

    def test_unknown_prefix_rejected(self, clean_model_env) -> None:
        with pytest.raises(ProviderError, match="allowed-prefix"):
            _validate_model_string("rogue-vendor/some-model")

    def test_env_override_expands_allowlist(self, clean_model_env) -> None:
        with patch.dict(os.environ, {"VERIDIAN_ALLOWED_MODELS": "rogue-vendor/,gemini/"}):
            _validate_model_string("rogue-vendor/some-model")
            _validate_model_string("gemini/foo")
            with pytest.raises(ProviderError):
                _validate_model_string("gpt-4o")

    def test_wildcard_disables_guard(self, clean_model_env) -> None:
        with patch.dict(os.environ, {"VERIDIAN_ALLOWED_MODELS": "*"}):
            _validate_model_string("anything-goes/here")

    def test_provider_constructor_rejects_url_model(self, clean_model_env) -> None:
        with pytest.raises(ProviderError):
            LiteLLMProvider(model="http://attacker.example")

    def test_fallback_models_also_validated(self, clean_model_env) -> None:
        with pytest.raises(ProviderError):
            LiteLLMProvider(
                model="gemini/gemini-2.5-flash",
                fallback_models=["https://attacker.example/v1"],
            )
