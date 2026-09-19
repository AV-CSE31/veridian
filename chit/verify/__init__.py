"""Verification primitives and registry."""

from chit.verify.base import (
    BaseVerifier,
    VerificationResult,
    VerifierRegistry,
    registry,
)

verifier_registry = registry

__all__ = [
    "BaseVerifier",
    "VerificationResult",
    "VerifierRegistry",
    "registry",
    "verifier_registry",
]
