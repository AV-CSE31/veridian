"""
veridian.identity.pki
──────────────────────
Sovereign Agent Identity — Ed25519 keypair management, signing, verification,
and the agent identity registry.

Uses cryptography library (PyCA) for Ed25519 operations.
Falls back to a pure-Python implementation hint if cryptography is missing.

Design constraints (CLAUDE.md):
- Raise from the exception hierarchy — only VeridianError subclasses.
- Dependency injection: PKIManager receives AgentIdentityRegistry.
- No global mutable state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from veridian.core.exceptions import (
    AgentIdentityNotFound,
    KeyRotationError,
    PKIError,
    SignatureVerificationError,
)
from veridian.identity.models import AgentIdentity, SignedMessage

log = logging.getLogger(__name__)

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTO_AVAILABLE = False


def _require_crypto() -> None:
    if not _CRYPTO_AVAILABLE:  # pragma: no cover
        raise PKIError(
            "PKI features require the 'cryptography' package. "
            "Install it with: pip install cryptography"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT KEY PAIR
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentKeyPair:
    """
    Ed25519 keypair for one agent instance.

    public_key_bytes  — raw 32-byte public key
    private_key_bytes — raw 32-byte private key seed (keep secret)
    """

    public_key_bytes: bytes
    private_key_bytes: bytes

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def generate(cls) -> AgentKeyPair:
        """Generate a fresh Ed25519 keypair."""
        _require_crypto()
        private_key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
        public_key: Ed25519PublicKey = private_key.public_key()
        private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        public_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return cls(public_key_bytes=public_bytes, private_key_bytes=private_bytes)

    @classmethod
    def from_private_key_bytes(cls, private_bytes: bytes) -> AgentKeyPair:
        """Reconstruct a keypair from raw private key bytes."""
        _require_crypto()
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        public_key = private_key.public_key()
        public_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return cls(public_key_bytes=public_bytes, private_key_bytes=private_bytes)

    # ── Sign / Verify ─────────────────────────────────────────────────────────

    def sign(self, message: bytes) -> bytes:
        """Sign a message. Returns 64-byte Ed25519 signature."""
        _require_crypto()
        private_key = Ed25519PrivateKey.from_private_bytes(self.private_key_bytes)
        sig: bytes = private_key.sign(message)
        return sig

    def verify(self, message: bytes, signature: bytes) -> None:
        """
        Verify a signature against this keypair's public key.
        Raises PKIError on invalid signature.
        """
        _require_crypto()
        try:
            public_key = Ed25519PublicKey.from_public_bytes(self.public_key_bytes)
            public_key.verify(signature, message)
        except (InvalidSignature, Exception) as exc:
            raise PKIError(f"Signature verification failed: {exc}") from exc

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()


# ─────────────────────────────────────────────────────────────────────────────
# AGENT IDENTITY REGISTRY
# ─────────────────────────────────────────────────────────────────────────────


class AgentIdentityRegistry:
    """
    In-memory registry mapping agent_id → AgentIdentity.

    For production persistence, serialise with to_dict() / from_dict() and
    persist via atomic write (same pattern as FeedbackStore).
    """

    def __init__(self) -> None:
        self._identities: dict[str, AgentIdentity] = {}

    def register(self, identity: AgentIdentity) -> None:
        """Register a new agent identity. Raises PKIError if already exists."""
        if identity.agent_id in self._identities:
            raise PKIError(
                f"Agent '{identity.agent_id}' already registered. Use rotate_key() to update."
            )
        self._identities[identity.agent_id] = identity
        log.info("identity.register agent=%s", identity.agent_id)

    def get(self, agent_id: str) -> AgentIdentity:
        """Return AgentIdentity. Raises AgentIdentityNotFound if missing."""
        if agent_id not in self._identities:
            raise AgentIdentityNotFound(agent_id)
        return self._identities[agent_id]

    def list_agents(self) -> list[AgentIdentity]:
        """Return all registered identities."""
        return list(self._identities.values())

    def revoke(self, agent_id: str) -> None:
        """Revoke an agent's identity. Raises AgentIdentityNotFound if missing."""
        identity = self.get(agent_id)
        identity.revoke()
        log.info("identity.revoke agent=%s", agent_id)

    def rotate_key(self, agent_id: str, new_identity: AgentIdentity) -> None:
        """
        Replace the public key for an agent with a new identity.

        The old identity is NOT revoked automatically — callers can revoke
        the old key explicitly after confirming the new key is working.

        Raises AgentIdentityNotFound if agent not registered.
        Raises KeyRotationError if agent is already revoked.
        """
        old = self.get(agent_id)  # raises AgentIdentityNotFound if absent
        if old.is_revoked:
            raise KeyRotationError(
                f"Cannot rotate key for revoked agent '{agent_id}'. "
                "Register a new agent identity instead."
            )
        self._identities[agent_id] = new_identity
        log.info("identity.rotate_key agent=%s", agent_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identities": {
                agent_id: identity.to_dict() for agent_id, identity in self._identities.items()
            }
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentIdentityRegistry:
        registry = cls()
        for agent_id, identity_dict in d.get("identities", {}).items():
            identity = AgentIdentity.from_dict(identity_dict)
            registry._identities[agent_id] = identity
        return registry


# ─────────────────────────────────────────────────────────────────────────────
# PKI MANAGER
# ─────────────────────────────────────────────────────────────────────────────


class PKIManager:
    """
    High-level PKI operations: sign and verify agent messages.

    Injected with an AgentIdentityRegistry for public-key lookup.
    """

    def __init__(self, registry: AgentIdentityRegistry) -> None:
        self._registry = registry

    def sign(self, payload: bytes, agent_id: str, keypair: AgentKeyPair) -> SignedMessage:
        """
        Sign a payload with the agent's keypair and return a SignedMessage.

        The signing agent must be registered in the registry.
        """
        # Confirm agent is registered (raises AgentIdentityNotFound otherwise)
        self._registry.get(agent_id)
        signature = keypair.sign(payload)
        signed = SignedMessage(
            message=payload,
            signature=signature,
            agent_id=agent_id,
        )
        log.debug("pki.sign agent=%s payload_len=%d", agent_id, len(payload))
        return signed

    def verify(self, signed: SignedMessage) -> None:
        """
        Verify a SignedMessage against the registry's public key.

        Raises:
          AgentIdentityNotFound   — agent_id not in registry
          SignatureVerificationError — signature invalid or agent revoked
        """
        identity = self._registry.get(signed.agent_id)  # raises AgentIdentityNotFound

        if identity.is_revoked:
            raise SignatureVerificationError(
                signed.agent_id,
                reason="agent identity has been revoked",
            )

        try:
            _require_crypto()
            public_key = Ed25519PublicKey.from_public_bytes(identity.public_key_bytes)
            public_key.verify(signed.signature, signed.message)
        except (PKIError, Exception) as exc:
            raise SignatureVerificationError(
                signed.agent_id,
                reason=str(exc),
            ) from exc

        log.debug("pki.verify agent=%s ok", signed.agent_id)
