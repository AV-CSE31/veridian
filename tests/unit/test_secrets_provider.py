"""
Tests for veridian.secrets — SecretsProvider ABC + EnvSecretsProvider.
TDD: RED phase.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from veridian.core.exceptions import SecretNotFound, SecretRotationFailed
from veridian.secrets.base import SecretsProvider
from veridian.secrets.env_provider import EnvSecretsProvider

# ── ABC contract ─────────────────────────────────────────────────────────────


class TestSecretsProviderABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            SecretsProvider()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        class Stub(SecretsProvider):
            provider_id: str = "stub"

            def get(self, secret_ref: str) -> str:
                return "value"

            def rotate_check(self) -> None:
                pass

            def list_refs(self) -> list[str]:
                return []

        s = Stub()
        assert s.get("x") == "value"
        assert s.provider_id == "stub"


# ── EnvSecretsProvider ───────────────────────────────────────────────────────


class TestEnvSecretsProviderConstruction:
    def test_creates_with_default_prefix(self) -> None:
        provider = EnvSecretsProvider()
        assert provider.provider_id == "env"
        assert provider._prefix == "VERIDIAN_"

    def test_creates_with_custom_prefix(self) -> None:
        provider = EnvSecretsProvider(env_prefix="MY_APP_")
        assert provider._prefix == "MY_APP_"


class TestEnvSecretsProviderGet:
    def test_reads_env_var_with_prefix(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_DB_PASSWORD": "s3cr3t"}):
            provider = EnvSecretsProvider()
            assert provider.get("db_password") == "s3cr3t"

    def test_reads_uppercase_ref(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_API_KEY": "key123"}):
            provider = EnvSecretsProvider()
            assert provider.get("api_key") == "key123"

    def test_raises_secret_not_found_for_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = EnvSecretsProvider()
            with pytest.raises(SecretNotFound, match="nonexistent"):
                provider.get("nonexistent")

    def test_custom_prefix_applied(self) -> None:
        with patch.dict(os.environ, {"APP_TOKEN": "tok"}):
            provider = EnvSecretsProvider(env_prefix="APP_")
            assert provider.get("token") == "tok"

    def test_never_returns_empty_string_as_secret(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_EMPTY": ""}):
            provider = EnvSecretsProvider()
            with pytest.raises(SecretNotFound, match="empty"):
                provider.get("empty")


class TestEnvSecretsProviderRotateCheck:
    def test_rotate_check_passes_when_all_registered_exist(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_KEY1": "v1", "VERIDIAN_KEY2": "v2"}):
            provider = EnvSecretsProvider()
            provider.register_required(["key1", "key2"])
            provider.rotate_check()  # should not raise

    def test_rotate_check_raises_when_required_missing(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_KEY1": "v1"}, clear=True):
            provider = EnvSecretsProvider()
            provider.register_required(["key1", "missing_key"])
            with pytest.raises(SecretRotationFailed, match="missing_key"):
                provider.rotate_check()

    def test_rotate_check_noop_when_no_required_keys(self) -> None:
        provider = EnvSecretsProvider()
        provider.rotate_check()  # no-op, should not raise


class TestEnvSecretsProviderListRefs:
    def test_lists_matching_env_vars(self) -> None:
        with patch.dict(
            os.environ,
            {
                "VERIDIAN_KEY1": "v1",
                "VERIDIAN_KEY2": "v2",
                "OTHER_VAR": "ignored",
            },
            clear=True,
        ):
            provider = EnvSecretsProvider()
            refs = provider.list_refs()
            assert "key1" in refs
            assert "key2" in refs
            assert "other_var" not in refs
