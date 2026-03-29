"""
veridian.identity
─────────────────
Sovereign Agent Identity (PKI) — Ed25519 keypairs, signing, verification,
agent identity registry, key rotation, and certificate chains.
"""

from veridian.identity.models import AgentIdentity, CertificateChain, SignedMessage
from veridian.identity.pki import AgentIdentityRegistry, AgentKeyPair, PKIManager

__all__ = [
    "AgentIdentity",
    "AgentIdentityRegistry",
    "AgentKeyPair",
    "CertificateChain",
    "PKIManager",
    "SignedMessage",
]
