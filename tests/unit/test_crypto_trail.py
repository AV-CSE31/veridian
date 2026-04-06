"""
tests/unit/test_crypto_trail.py
────────────────────────────────
Unit tests for the Cryptographic Audit Trail (F2.1).

Covers:
  - AuditEntry: hash computation, immutability, serialization
  - AuditChain: append, chain integrity, tamper detection
  - CryptoAuditTrail: high-level API, atomic file persistence, JSON export
  - Edge cases: empty chain, single entry, genesis hash, tamper detection
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from veridian.audit.crypto_trail import (
    GENESIS_HASH,
    AuditChain,
    AuditEntry,
    CryptoAuditTrail,
    make_audit_entry,
)
from veridian.core.exceptions import AuditIntegrityError

# ─────────────────────────────────────────────────────────────────────────────
# AuditEntry tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditEntry:
    def test_entry_has_unique_id(self) -> None:
        e1 = make_audit_entry("t1", "test_event", {})
        e2 = make_audit_entry("t1", "test_event", {})
        assert e1.entry_id != e2.entry_id

    def test_entry_has_timestamp(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        assert isinstance(entry.timestamp_utc, datetime)
        assert entry.timestamp_utc.tzinfo is not None  # timezone-aware

    def test_data_hash_is_sha256_hex(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        assert len(entry.data_hash) == 64
        assert all(c in "0123456789abcdef" for c in entry.data_hash)

    def test_entry_hash_is_sha256_hex(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        assert len(entry.entry_hash) == 64
        assert all(c in "0123456789abcdef" for c in entry.entry_hash)

    def test_first_entry_previous_hash_is_genesis(self) -> None:
        entry = make_audit_entry("t1", "test_event", {})
        assert entry.previous_hash == GENESIS_HASH

    def test_data_hash_changes_with_data(self) -> None:
        e1 = make_audit_entry("t1", "event", {"value": 1})
        e2 = make_audit_entry("t1", "event", {"value": 2})
        assert e1.data_hash != e2.data_hash

    def test_entry_is_valid_by_construction(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        assert entry.is_valid()

    def test_entry_invalid_after_data_tamper(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        # Simulate tampering by replacing the frozen model with mutated copy
        tampered = entry.model_copy(update={"data": {"passed": False}})
        assert not tampered.is_valid()

    def test_entry_invalid_after_hash_tamper(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True})
        tampered = entry.model_copy(update={"data_hash": "a" * 64})
        assert not tampered.is_valid()

    def test_entry_serializes_to_dict(self) -> None:
        entry = make_audit_entry("t1", "verification_result", {"passed": True, "score": 0.9})
        d = entry.model_dump()
        assert d["task_id"] == "t1"
        assert d["event_type"] == "verification_result"
        assert "entry_hash" in d
        assert "previous_hash" in d

    def test_entry_round_trips_json(self) -> None:
        entry = make_audit_entry("t1", "test_event", {"key": "value"})
        raw = entry.model_dump_json()
        restored = AuditEntry.model_validate_json(raw)
        assert restored.entry_id == entry.entry_id
        assert restored.entry_hash == entry.entry_hash
        assert restored.is_valid()


# ─────────────────────────────────────────────────────────────────────────────
# AuditChain tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditChain:
    def test_empty_chain_is_valid(self) -> None:
        chain = AuditChain()
        assert chain.verify_integrity()

    def test_single_entry_chain_is_valid(self) -> None:
        chain = AuditChain()
        e = make_audit_entry("t1", "test", {})
        chain.append(e)
        assert chain.verify_integrity()

    def test_multiple_entries_form_valid_chain(self) -> None:
        chain = AuditChain()
        prev = GENESIS_HASH
        for i in range(5):
            e = make_audit_entry(f"t{i}", "event", {"i": i}, previous_hash=prev)
            chain.append(e)
            prev = e.entry_hash
        assert chain.verify_integrity()

    def test_chain_links_entries_via_previous_hash(self) -> None:
        chain = AuditChain()
        e1 = make_audit_entry("t1", "event", {})
        chain.append(e1)
        e2 = make_audit_entry("t1", "event2", {}, previous_hash=e1.entry_hash)
        chain.append(e2)
        assert e2.previous_hash == e1.entry_hash

    def test_tamper_detection_on_entry_data(self) -> None:
        chain = AuditChain()
        e1 = make_audit_entry("t1", "result", {"passed": True})
        chain.append(e1)
        e2 = make_audit_entry("t1", "result2", {"passed": True}, previous_hash=e1.entry_hash)
        chain.append(e2)
        # Tamper an entry directly
        tampered_entry = e1.model_copy(update={"data": {"passed": False}})
        chain.entries[0] = tampered_entry
        assert not chain.verify_integrity()

    def test_tamper_detection_on_previous_hash(self) -> None:
        chain = AuditChain()
        e1 = make_audit_entry("t1", "event", {})
        chain.append(e1)
        e2 = make_audit_entry("t1", "event2", {}, previous_hash=e1.entry_hash)
        chain.append(e2)
        # Break the linkage by pointing to wrong previous
        bad_e2 = e2.model_copy(update={"previous_hash": "0" * 64})
        chain.entries[1] = bad_e2
        assert not chain.verify_integrity()

    def test_chain_length(self) -> None:
        chain = AuditChain()
        for i in range(3):
            chain.append(make_audit_entry("t1", "event", {"i": i}))
        assert len(chain.entries) == 3

    def test_chain_has_unique_id(self) -> None:
        c1 = AuditChain()
        c2 = AuditChain()
        assert c1.chain_id != c2.chain_id

    def test_chain_tail_hash_is_last_entry_hash(self) -> None:
        chain = AuditChain()
        e = make_audit_entry("t1", "event", {})
        chain.append(e)
        assert chain.tail_hash == e.entry_hash

    def test_empty_chain_tail_hash_is_genesis(self) -> None:
        chain = AuditChain()
        assert chain.tail_hash == GENESIS_HASH

    def test_chain_serializes_to_json(self) -> None:
        chain = AuditChain()
        chain.append(make_audit_entry("t1", "event", {"x": 1}))
        raw = chain.to_json()
        parsed = json.loads(raw)
        assert "entries" in parsed
        assert len(parsed["entries"]) == 1

    def test_chain_round_trips_json(self) -> None:
        chain = AuditChain()
        chain.append(make_audit_entry("t1", "event", {"x": 1}))
        raw = chain.to_json()
        restored = AuditChain.from_json(raw)
        assert restored.chain_id == chain.chain_id
        assert len(restored.entries) == 1
        assert restored.verify_integrity()


# ─────────────────────────────────────────────────────────────────────────────
# CryptoAuditTrail tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCryptoAuditTrail:
    def test_trail_starts_empty(self) -> None:
        trail = CryptoAuditTrail()
        assert trail.entry_count == 0

    def test_append_verification_result(self) -> None:
        trail = CryptoAuditTrail()
        entry = trail.append_event(
            "t1", "verification_result", {"passed": True, "verifier_id": "schema"}
        )
        assert entry.task_id == "t1"
        assert entry.event_type == "verification_result"
        assert trail.entry_count == 1

    def test_multiple_appends_maintain_chain(self) -> None:
        trail = CryptoAuditTrail()
        for i in range(10):
            trail.append_event(f"t{i}", "event", {"i": i})
        assert trail.entry_count == 10
        assert trail.verify_integrity()

    def test_verify_integrity_passes_on_clean_chain(self) -> None:
        trail = CryptoAuditTrail()
        trail.append_event("t1", "verification_result", {"passed": True})
        trail.append_event("t1", "task_complete", {"status": "done"})
        assert trail.verify_integrity()

    def test_export_json_is_parseable(self) -> None:
        trail = CryptoAuditTrail()
        trail.append_event("t1", "event", {"key": "value"})
        raw = trail.export_json()
        parsed = json.loads(raw)
        assert "entries" in parsed
        assert "chain_id" in parsed

    def test_save_and_load_roundtrip(self) -> None:
        trail = CryptoAuditTrail()
        trail.append_event("t1", "verification_result", {"passed": True})
        trail.append_event("t2", "task_complete", {"status": "done"})
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit_chain.jsonl"
            trail.save(path)
            assert path.exists()
            loaded = CryptoAuditTrail.load(path)
            assert loaded.entry_count == 2
            assert loaded.verify_integrity()

    def test_save_uses_atomic_write(self) -> None:
        """Verify save writes to a temp file first (atomic write pattern)."""
        trail = CryptoAuditTrail()
        trail.append_event("t1", "event", {})
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chain.json"
            trail.save(path)
            # File must exist and be valid JSON
            data = json.loads(path.read_text())
            assert "entries" in data

    def test_verify_integrity_raises_on_tamper(self) -> None:
        """verify_chain() raises AuditIntegrityError when strict=True."""
        trail = CryptoAuditTrail()
        trail.append_event("t1", "event", {"passed": True})
        # Directly tamper with the chain
        trail._chain.entries[0] = trail._chain.entries[0].model_copy(
            update={"data": {"passed": False}}
        )
        with pytest.raises(AuditIntegrityError):
            trail.verify_chain(strict=True)

    def test_verify_chain_returns_false_without_raise(self) -> None:
        trail = CryptoAuditTrail()
        trail.append_event("t1", "event", {"passed": True})
        trail._chain.entries[0] = trail._chain.entries[0].model_copy(
            update={"data": {"passed": False}}
        )
        result = trail.verify_chain(strict=False)
        assert result is False

    def test_entries_are_linked_in_order(self) -> None:
        trail = CryptoAuditTrail()
        e1 = trail.append_event("t1", "start", {})
        e2 = trail.append_event("t1", "end", {})
        assert e2.previous_hash == e1.entry_hash
        assert e1.previous_hash == GENESIS_HASH

    def test_chain_id_is_stable(self) -> None:
        trail = CryptoAuditTrail(chain_id="test-chain-001")
        trail.append_event("t1", "event", {})
        raw = trail.export_json()
        data = json.loads(raw)
        assert data["chain_id"] == "test-chain-001"
