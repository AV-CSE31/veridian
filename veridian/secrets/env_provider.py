"""
veridian.secrets.env_provider
─────────────────────────────
EnvSecretsProvider — reads secrets from environment variables.

The only provider usable in CI without external services.
Never caches secret values in memory — always reads from os.environ.

Usage::

    provider = EnvSecretsProvider(env_prefix="VERIDIAN_")
    api_key = provider.get("openai_api_key")  # reads VERIDIAN_OPENAI_API_KEY
"""

from __future__ import annotations

import os

from veridian.core.exceptions import SecretNotFound, SecretRotationFailed
from veridian.secrets.base import SecretsProvider

__all__ = ["EnvSecretsProvider"]


class EnvSecretsProvider(SecretsProvider):
    """CI-friendly secrets provider reading from environment variables.

    Constructs env var names as: ``{prefix}{secret_ref.upper()}``.
    Raises SecretNotFound if the env var is missing or empty.
    """

    provider_id: str = "env"

    def __init__(self, env_prefix: str = "VERIDIAN_") -> None:
        self._prefix = env_prefix
        self._required_refs: list[str] = []

    def get(self, secret_ref: str) -> str:
        """Read secret from environment variable.

        Args:
            secret_ref: Key name (lowercased). Env var is ``prefix + ref.upper()``.

        Returns:
            Secret value.

        Raises:
            SecretNotFound: If env var missing or empty.
        """
        env_var = f"{self._prefix}{secret_ref.upper()}"
        value = os.environ.get(env_var)
        if value is None or value == "":
            raise SecretNotFound(secret_ref)
        return value

    def rotate_check(self) -> None:
        """Validate all registered required secrets still exist.

        Raises:
            SecretRotationFailed: If any required secret is missing.
        """
        missing: list[str] = []
        for ref in self._required_refs:
            env_var = f"{self._prefix}{ref.upper()}"
            if not os.environ.get(env_var):
                missing.append(ref)
        if missing:
            raise SecretRotationFailed(
                f"Required secrets missing from environment: {', '.join(missing)}"
            )

    def register_required(self, refs: list[str]) -> None:
        """Register secret references that must always be present.

        Args:
            refs: List of secret reference keys to validate on rotate_check().
        """
        self._required_refs = list(refs)

    def list_refs(self) -> list[str]:
        """Return all env vars matching the prefix as lowercased refs."""
        refs: list[str] = []
        for key in os.environ:
            if key.startswith(self._prefix):
                ref = key[len(self._prefix) :].lower()
                if ref:
                    refs.append(ref)
        return refs
