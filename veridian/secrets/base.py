"""
veridian.secrets.base
─────────────────────
SecretsProvider ABC — abstract base for all secret management backends.

Concrete implementations: EnvSecretsProvider (CI), VaultSecretsProvider,
AWSSecretsProvider, AzureSecretsProvider (future phases).

Contract:
  - get() returns the secret value or raises SecretNotFound
  - rotate_check() is called on EVERY before_task, not once per run
  - list_refs() returns all available secret references
  - provider_id is a ClassVar identifying the backend
"""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["SecretsProvider"]


class SecretsProvider(ABC):
    """Abstract base class for secret management providers."""

    provider_id: str = ""

    @abstractmethod
    def get(self, secret_ref: str) -> str:
        """Retrieve a secret by reference key.

        Args:
            secret_ref: Key name (e.g. "api_key", "db_password").

        Returns:
            Secret value as string.

        Raises:
            SecretNotFound: If secret key does not exist or is empty.
            SecretsProviderError: If provider is unavailable.
        """

    @abstractmethod
    def rotate_check(self) -> None:
        """Validate secret freshness and rotation status.

        Called on every before_task — not once per run. Credentials
        can rotate mid-run.

        Raises:
            SecretRotationFailed: If credentials are stale or expired.
        """

    @abstractmethod
    def list_refs(self) -> list[str]:
        """Return all available secret reference keys."""
