"""
veridian.secrets
────────────────
Secret management infrastructure — pluggable providers for credential
retrieval, rotation checking, and output scrubbing.

Public API::

    from veridian.secrets import SecretsProvider, EnvSecretsProvider
"""

from veridian.secrets.base import SecretsProvider
from veridian.secrets.env_provider import EnvSecretsProvider

__all__ = ["SecretsProvider", "EnvSecretsProvider"]
