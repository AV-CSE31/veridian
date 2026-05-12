"""
tests.unit.test_phase1a_security_guards
───────────────────────────────────────
Acceptance tests for the Phase 1.A security hardening additions:

* TrustedExecutor and BashExitCodeVerifier scrub the child-process env so
  parent secrets cannot leak into agent-issued shells.
* LiteLLMProvider rejects URL-shaped model strings and enforces a provider
  prefix allowlist controllable via ``VERIDIAN_ALLOWED_MODELS``.
* OTLP exporter rejects non-loopback plain-HTTP endpoints unless opted in.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from veridian.core.exceptions import ProviderError, VeridianConfigError
from veridian.loop.trusted_executor import DEFAULT_ENV_ALLOWLIST, TrustedExecutor
from veridian.observability.otlp_exporter import (
    OTLPConfig,
    _validate_otlp_endpoint,
    configure_otlp_tracer,
)
from veridian.providers.litellm_provider import (
    LiteLLMProvider,
    _validate_model_string,
)
from veridian.verify.builtin.bash import BashExitCodeVerifier

# ── TrustedExecutor env scrubbing ───────────────────────────────────────────


class TestTrustedExecutorEnv:
    def test_default_strips_unrelated_env_vars(self, tmp_path) -> None:
        executor = TrustedExecutor(working_dir=str(tmp_path), timeout_seconds=5)
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-leaked", "AWS_SECRET_ACCESS_KEY": "leaked"},
            clear=False,
        ):
            output = executor.run("env | grep -E '^(OPENAI|AWS)_' || true")
        assert "sk-leaked" not in output.stdout
        assert "AWS_SECRET" not in output.stdout

    def test_path_passes_through(self, tmp_path) -> None:
        executor = TrustedExecutor(working_dir=str(tmp_path), timeout_seconds=5)
        output = executor.run("echo $PATH")
        assert output.stdout.strip()  # PATH is in DEFAULT_ENV_ALLOWLIST

    def test_inherit_env_opt_in_still_works(self, tmp_path) -> None:
        executor = TrustedExecutor(working_dir=str(tmp_path), timeout_seconds=5, inherit_env=True)
        with patch.dict(os.environ, {"VERIDIAN_TEST_LEAK": "yes-via-opt-in"}, clear=False):
            output = executor.run("echo $VERIDIAN_TEST_LEAK")
        assert "yes-via-opt-in" in output.stdout

    def test_custom_allowlist_extends_defaults(self, tmp_path) -> None:
        executor = TrustedExecutor(
            working_dir=str(tmp_path),
            timeout_seconds=5,
            env_allowlist=(*DEFAULT_ENV_ALLOWLIST, "VERIDIAN_TEST_ALLOWED"),
        )
        with patch.dict(
            os.environ,
            {"VERIDIAN_TEST_ALLOWED": "exposed", "OPENAI_API_KEY": "sk-still-stripped"},
            clear=False,
        ):
            output = executor.run("echo allowed=$VERIDIAN_TEST_ALLOWED openai=$OPENAI_API_KEY")
        assert "allowed=exposed" in output.stdout
        assert "openai=" in output.stdout
        assert "sk-still-stripped" not in output.stdout


# ── BashExitCodeVerifier ────────────────────────────────────────────────────


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
            command="env | grep -E '^(OPENAI|AWS)_' && exit 1 || exit 0",
            timeout_seconds=5,
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-secret", "AWS_SECRET_ACCESS_KEY": "leak"},
            clear=False,
        ):
            outcome = v.verify(Task(title="t"), TaskResult(raw_output=""))
        assert outcome.passed  # secrets were stripped, grep finds nothing


# ── LiteLLM model allowlist ─────────────────────────────────────────────────


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


# ── OTLP endpoint validation ────────────────────────────────────────────────


class TestOTLPEndpointGuard:
    def test_https_endpoint_accepted(self) -> None:
        _validate_otlp_endpoint("https://collector.example:4318/v1/traces", allow_http=False)

    def test_http_loopback_accepted_without_opt_in(self) -> None:
        _validate_otlp_endpoint("http://localhost:4318/v1/traces", allow_http=False)
        _validate_otlp_endpoint("http://127.0.0.1:4318/v1/traces", allow_http=False)

    def test_http_external_rejected_by_default(self) -> None:
        with pytest.raises(ValueError, match="plain HTTP"):
            _validate_otlp_endpoint("http://collector.example:4318/v1/traces", allow_http=False)

    def test_http_external_accepted_with_opt_in(self) -> None:
        # Should not raise — parameter opt-in.
        _validate_otlp_endpoint("http://collector.example:4318/v1/traces", allow_http=True)

    def test_http_external_accepted_with_env_opt_in(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_OTLP_ALLOW_HTTP": "1"}):
            _validate_otlp_endpoint("http://collector.example:4318/v1/traces", allow_http=False)

    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="http\\(s\\) URL"):
            _validate_otlp_endpoint("file:///etc/passwd", allow_http=True)

    def test_configure_otlp_tracer_default_allows_localhost(self, tmp_path) -> None:
        tracer = configure_otlp_tracer(
            config=OTLPConfig(),  # endpoint=http://localhost:4318/v1/traces
            trace_file=tmp_path / "trace.jsonl",
            use_otel=False,
        )
        assert tracer is not None

    def test_configure_otlp_tracer_rejects_external_http(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="plain HTTP"):
            configure_otlp_tracer(
                config=OTLPConfig(endpoint="http://attacker.example:4318/v1/traces"),
                trace_file=tmp_path / "trace.jsonl",
                use_otel=False,
            )
