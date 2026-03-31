"""
veridian.identity.models
─────────────────────────
Domain models for Sovereign Agent Identity (PKI).

AgentIdentity  — immutable identity record tied to one Ed25519 public key.
SignedMessage   — a message payload paired with its Ed25519 signature.
CertificateChain — ordered chain of AgentIdentity forming a trust hierarchy.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from veridian.core.exceptions import PKIError

# ─────────────────────────────────────────────────────────────────────────────
# AGENT IDENTITY
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentIdentity:
    """
    Represents one agent's public identity.

    public_key_bytes — raw Ed25519 public key (32 bytes).
    parent_id        — agent_id of the issuing/parent agent (None = root).
    is_revoked       — True once the identity has been revoked.
    """

    agent_id: str
    public_key_bytes: bytes
    parent_id: str | None = None
    is_revoked: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def revoke(self) -> None:
        """Mark this identity as revoked."""
        self.is_revoked = True

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "public_key_b64": base64.b64encode(self.public_key_bytes).decode(),
            "parent_id": self.parent_id,
            "is_revoked": self.is_revoked,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentIdentity:
        return cls(
            agent_id=d["agent_id"],
            public_key_bytes=base64.b64decode(d["public_key_b64"]),
            parent_id=d.get("parent_id"),
            is_revoked=d.get("is_revoked", False),
            created_at=d.get("created_at", datetime.now(UTC).isoformat()),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SIGNED MESSAGE
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SignedMessage:
    """
    A message payload with its Ed25519 signature and the signing agent's ID.
    """

    message: bytes
    signature: bytes
    agent_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_b64": base64.b64encode(self.message).decode(),
            "signature_b64": base64.b64encode(self.signature).decode(),
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SignedMessage:
        return cls(
            message=base64.b64decode(d["message_b64"]),
            signature=base64.b64decode(d["signature_b64"]),
            agent_id=d["agent_id"],
            timestamp=d.get("timestamp", datetime.now(UTC).isoformat()),
        )


# ─────────────────────────────────────────────────────────────────────────────
# CERTIFICATE CHAIN
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CertificateChain:
    """
    Ordered list of AgentIdentity representing a trust hierarchy.

    The first element is the root (no parent).
    The last element is the leaf (the agent whose trust is being established).
    """

    _identities: list[AgentIdentity]

    def __init__(self, identities: list[AgentIdentity]) -> None:
        if not identities:
            raise PKIError("CertificateChain cannot be empty")
        self._identities = list(identities)

    def __len__(self) -> int:
        return len(self._identities)

    @property
    def root(self) -> AgentIdentity:
        return self._identities[0]

    @property
    def leaf(self) -> AgentIdentity:
        return self._identities[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": [identity.to_dict() for identity in self._identities],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CertificateChain:
        identities = [AgentIdentity.from_dict(item) for item in d["chain"]]
        return cls(identities)
