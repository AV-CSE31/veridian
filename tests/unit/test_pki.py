"""
tests.unit.test_pki
────────────────────
Sovereign Agent Identity (PKI) — Ed25519 keypairs, signing, verification,
registry, key rotation, certificate chains.
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import (
    AgentIdentityNotFound,
    KeyRotationError,
    PKIError,
    SignatureVerificationError,
)
from veridian.identity.models import AgentIdentity, CertificateChain, SignedMessage
from veridian.identity.pki import AgentIdentityRegistry, AgentKeyPair, PKIManager

# ── AgentKeyPair ──────────────────────────────────────────────────────────────


class TestAgentKeyPair:
    def test_generate_produces_valid_pair(self) -> None:
        kp = AgentKeyPair.generate()
        assert kp.public_key_bytes
        assert kp.private_key_bytes
        assert len(kp.public_key_bytes) == 32  # Ed25519 public key is 32 bytes

    def test_two_generates_are_unique(self) -> None:
        kp1 = AgentKeyPair.generate()
        kp2 = AgentKeyPair.generate()
        assert kp1.public_key_bytes != kp2.public_key_bytes

    def test_sign_produces_bytes(self) -> None:
        kp = AgentKeyPair.generate()
        sig = kp.sign(b"hello world")
        assert isinstance(sig, bytes)
        assert len(sig) == 64  # Ed25519 signature is 64 bytes

    def test_sign_different_messages_different_sigs(self) -> None:
        kp = AgentKeyPair.generate()
        sig1 = kp.sign(b"message one")
        sig2 = kp.sign(b"message two")
        assert sig1 != sig2

    def test_verify_valid_signature(self) -> None:
        kp = AgentKeyPair.generate()
        message = b"agent output payload"
        sig = kp.sign(message)
        # Should not raise
        kp.verify(message, sig)

    def test_verify_invalid_signature_raises(self) -> None:
        kp = AgentKeyPair.generate()
        message = b"real message"
        kp.sign(message)
        with pytest.raises(PKIError):
            kp.verify(message, b"\x00" * 64)

    def test_verify_tampered_message_raises(self) -> None:
        kp = AgentKeyPair.generate()
        message = b"original"
        sig = kp.sign(message)
        with pytest.raises(PKIError):
            kp.verify(b"tampered", sig)

    def test_from_private_key_bytes_round_trip(self) -> None:
        kp = AgentKeyPair.generate()
        private_bytes = kp.private_key_bytes
        kp2 = AgentKeyPair.from_private_key_bytes(private_bytes)
        assert kp2.public_key_bytes == kp.public_key_bytes

    def test_public_key_hex(self) -> None:
        kp = AgentKeyPair.generate()
        hex_key = kp.public_key_hex
        assert len(hex_key) == 64  # 32 bytes → 64 hex chars
        assert all(c in "0123456789abcdef" for c in hex_key)


# ── AgentIdentity (model) ─────────────────────────────────────────────────────


class TestAgentIdentityModel:
    def test_construct(self) -> None:
        kp = AgentKeyPair.generate()
        identity = AgentIdentity(
            agent_id="agent-001",
            public_key_bytes=kp.public_key_bytes,
        )
        assert identity.agent_id == "agent-001"
        assert identity.is_revoked is False

    def test_revoke(self) -> None:
        kp = AgentKeyPair.generate()
        identity = AgentIdentity("agent-001", kp.public_key_bytes)
        identity.revoke()
        assert identity.is_revoked is True

    def test_serialise_round_trip(self) -> None:
        kp = AgentKeyPair.generate()
        identity = AgentIdentity("agent-001", kp.public_key_bytes)
        d = identity.to_dict()
        identity2 = AgentIdentity.from_dict(d)
        assert identity2.agent_id == identity.agent_id
        assert identity2.public_key_bytes == identity.public_key_bytes
        assert identity2.is_revoked == identity.is_revoked


# ── SignedMessage (model) ─────────────────────────────────────────────────────


class TestSignedMessage:
    def test_construct(self) -> None:
        sm = SignedMessage(
            message=b"payload",
            signature=b"\x01" * 64,
            agent_id="agent-001",
        )
        assert sm.agent_id == "agent-001"

    def test_serialise_round_trip(self) -> None:
        sm = SignedMessage(message=b"hello", signature=b"\xab" * 64, agent_id="a1")
        d = sm.to_dict()
        sm2 = SignedMessage.from_dict(d)
        assert sm2.message == sm.message
        assert sm2.signature == sm.signature
        assert sm2.agent_id == sm.agent_id


# ── AgentIdentityRegistry ─────────────────────────────────────────────────────


class TestAgentIdentityRegistry:
    def test_register_and_get(self) -> None:
        registry = AgentIdentityRegistry()
        kp = AgentKeyPair.generate()
        identity = AgentIdentity("agent-001", kp.public_key_bytes)
        registry.register(identity)
        retrieved = registry.get("agent-001")
        assert retrieved.agent_id == "agent-001"

    def test_get_not_found_raises(self) -> None:
        registry = AgentIdentityRegistry()
        with pytest.raises(AgentIdentityNotFound):
            registry.get("nonexistent")

    def test_list_agents(self) -> None:
        registry = AgentIdentityRegistry()
        for i in range(3):
            kp = AgentKeyPair.generate()
            registry.register(AgentIdentity(f"agent-{i:03d}", kp.public_key_bytes))
        agents = registry.list_agents()
        assert len(agents) == 3

    def test_register_duplicate_raises(self) -> None:
        registry = AgentIdentityRegistry()
        kp = AgentKeyPair.generate()
        identity = AgentIdentity("agent-001", kp.public_key_bytes)
        registry.register(identity)
        with pytest.raises(PKIError, match="already registered"):
            registry.register(identity)

    def test_revoke(self) -> None:
        registry = AgentIdentityRegistry()
        kp = AgentKeyPair.generate()
        registry.register(AgentIdentity("agent-001", kp.public_key_bytes))
        registry.revoke("agent-001")
        identity = registry.get("agent-001")
        assert identity.is_revoked is True

    def test_revoke_nonexistent_raises(self) -> None:
        registry = AgentIdentityRegistry()
        with pytest.raises(AgentIdentityNotFound):
            registry.revoke("ghost")

    def test_rotate_key(self) -> None:
        registry = AgentIdentityRegistry()
        kp1 = AgentKeyPair.generate()
        registry.register(AgentIdentity("agent-001", kp1.public_key_bytes))
        kp2 = AgentKeyPair.generate()
        new_identity = AgentIdentity("agent-001", kp2.public_key_bytes)
        registry.rotate_key("agent-001", new_identity)
        retrieved = registry.get("agent-001")
        assert retrieved.public_key_bytes == kp2.public_key_bytes

    def test_rotate_key_nonexistent_raises(self) -> None:
        registry = AgentIdentityRegistry()
        kp = AgentKeyPair.generate()
        with pytest.raises(AgentIdentityNotFound):
            registry.rotate_key("ghost", AgentIdentity("ghost", kp.public_key_bytes))

    def test_rotate_revoked_agent_raises(self) -> None:
        registry = AgentIdentityRegistry()
        kp1 = AgentKeyPair.generate()
        registry.register(AgentIdentity("agent-001", kp1.public_key_bytes))
        registry.revoke("agent-001")
        kp2 = AgentKeyPair.generate()
        with pytest.raises(KeyRotationError, match="revoked"):
            registry.rotate_key("agent-001", AgentIdentity("agent-001", kp2.public_key_bytes))


# ── PKIManager ────────────────────────────────────────────────────────────────


class TestPKIManager:
    def _setup(self) -> tuple[PKIManager, AgentKeyPair, str]:
        registry = AgentIdentityRegistry()
        kp = AgentKeyPair.generate()
        agent_id = "agent-001"
        registry.register(AgentIdentity(agent_id, kp.public_key_bytes))
        manager = PKIManager(registry)
        return manager, kp, agent_id

    def test_sign_and_verify(self) -> None:
        manager, kp, agent_id = self._setup()
        payload = b"agent output"
        signed = manager.sign(payload, agent_id, kp)
        # Should not raise
        manager.verify(signed)

    def test_verify_tampered_raises(self) -> None:
        manager, kp, agent_id = self._setup()
        signed = manager.sign(b"original", agent_id, kp)
        tampered = SignedMessage(
            message=b"tampered",
            signature=signed.signature,
            agent_id=agent_id,
        )
        with pytest.raises(SignatureVerificationError):
            manager.verify(tampered)

    def test_verify_unknown_agent_raises(self) -> None:
        manager, kp, agent_id = self._setup()
        AgentKeyPair.generate()
        signed = manager.sign(b"payload", agent_id, kp)
        # Craft a message with an unknown agent_id
        unknown_signed = SignedMessage(
            message=signed.message,
            signature=signed.signature,
            agent_id="unknown-agent",
        )
        with pytest.raises(AgentIdentityNotFound):
            manager.verify(unknown_signed)

    def test_verify_revoked_agent_raises(self) -> None:
        manager, kp, agent_id = self._setup()
        signed = manager.sign(b"payload", agent_id, kp)
        manager._registry.revoke(agent_id)
        with pytest.raises(SignatureVerificationError, match="revoked"):
            manager.verify(signed)

    def test_sign_returns_signed_message(self) -> None:
        manager, kp, agent_id = self._setup()
        signed = manager.sign(b"hello", agent_id, kp)
        assert isinstance(signed, SignedMessage)
        assert signed.agent_id == agent_id
        assert signed.message == b"hello"


# ── CertificateChain ──────────────────────────────────────────────────────────


class TestCertificateChain:
    def test_build_chain(self) -> None:
        root_kp = AgentKeyPair.generate()
        child_kp = AgentKeyPair.generate()
        root = AgentIdentity("root-agent", root_kp.public_key_bytes)
        child = AgentIdentity("child-agent", child_kp.public_key_bytes, parent_id="root-agent")
        chain = CertificateChain([root, child])
        assert len(chain) == 2

    def test_chain_root_has_no_parent(self) -> None:
        root_kp = AgentKeyPair.generate()
        root = AgentIdentity("root", root_kp.public_key_bytes)
        chain = CertificateChain([root])
        assert chain.root.parent_id is None

    def test_chain_leaf_has_parent(self) -> None:
        root_kp = AgentKeyPair.generate()
        child_kp = AgentKeyPair.generate()
        root = AgentIdentity("root", root_kp.public_key_bytes)
        child = AgentIdentity("child", child_kp.public_key_bytes, parent_id="root")
        chain = CertificateChain([root, child])
        assert chain.leaf.parent_id == "root"

    def test_empty_chain_raises(self) -> None:
        with pytest.raises(PKIError, match="empty"):
            CertificateChain([])

    def test_chain_serialise_round_trip(self) -> None:
        root_kp = AgentKeyPair.generate()
        child_kp = AgentKeyPair.generate()
        root = AgentIdentity("root", root_kp.public_key_bytes)
        child = AgentIdentity("child", child_kp.public_key_bytes, parent_id="root")
        chain = CertificateChain([root, child])
        d = chain.to_dict()
        chain2 = CertificateChain.from_dict(d)
        assert len(chain2) == 2
        assert chain2.root.agent_id == "root"
        assert chain2.leaf.agent_id == "child"
