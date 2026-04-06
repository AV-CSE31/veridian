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
from veridian.secrets.pii_policy import BUILTIN_PATTERNS, PIIMatch, PIIPattern, PIIPolicy
from veridian.secrets.trace_filter import TraceFilter

__all__ = [
    "BUILTIN_PATTERNS",
    "EnvSecretsProvider",
    "PIIMatch",
    "PIIPattern",
    "PIIPolicy",
    "SecretsProvider",
    "TraceFilter",
]
