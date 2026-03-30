"""
Tests for veridian.observability.proof_chain — Cryptographic audit chain.
TDD: RED phase.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from veridian.observability.proof_chain import ProofChain, ProofEntry


# ── ProofEntry ──────────────────────────────────────────────────────────────


class TestProofEntry:
    def test_creates_entry_with_all_fields(self) -> None:
        entry = ProofEntry(
            task_id="t1",
            task_spec_hash="abc123",
            verifier_config_hash="def456",
            model_version="gemini/gemini-2.5-flash",
            input_hash="inp123",
            output_hash="out456",
            verification_evidence={"passed": True},
            policy_attestation=["safety_v1"],
        )
        assert entry.task_id == "t1"
        assert entry.model_version == "gemini/gemini-2.5-flash"

    def test_entry_to_dict(self) -> None:
        entry = ProofEntry(task_id="t1", task_spec_hash="abc")
        d = entry.to_dict()
        assert d["task_id"] == "t1"
        assert "timestamp" in d
        assert "previous_hash" in d

    def test_entry_computes_hash(self) -> None:
        entry = ProofEntry(task_id="t1", task_spec_hash="abc")
        h = entry.compute_hash()
        assert len(h) == 64  # SHA-256 hex

    def test_different_entries_have_different_hashes(self) -> None:
        e1 = ProofEntry(task_id="t1", task_spec_hash="abc")
        e2 = ProofEntry(task_id="t2", task_spec_hash="def")
        assert e1.compute_hash() != e2.compute_hash()


# ── ProofChain ──────────────────────────────────────────────────────────────


class TestProofChain:
    def test_creates_empty_chain(self) -> None:
        chain = ProofChain()
        assert len(chain) == 0

    def test_append_entry(self) -> None:
        chain = ProofChain()
        entry = ProofEntry(task_id="t1", task_spec_hash="abc")
        chain.append(entry)
        assert len(chain) == 1

    def test_chain_links_entries(self) -> None:
        chain = ProofChain()
        e1 = ProofEntry(task_id="t1", task_spec_hash="abc")
        chain.append(e1)
        e2 = ProofEntry(task_id="t2", task_spec_hash="def")
        chain.append(e2)
        assert e2.previous_hash == e1.compute_hash()

    def test_verify_intact_chain(self) -> None:
        chain = ProofChain()
        for i in range(5):
            chain.append(ProofEntry(task_id=f"t{i}", task_spec_hash=f"hash{i}"))
        assert chain.verify() is True

    def test_verify_detects_tampering(self) -> None:
        chain = ProofChain()
        for i in range(5):
            chain.append(ProofEntry(task_id=f"t{i}", task_spec_hash=f"hash{i}"))
        # Tamper with entry 2
        chain._entries[2].task_spec_hash = "TAMPERED"
        assert chain.verify() is False

    def test_verify_empty_chain(self) -> None:
        chain = ProofChain()
        assert chain.verify() is True

    def test_save_and_load(self, tmp_path: Path) -> None:
        chain = ProofChain()
        for i in range(3):
            chain.append(ProofEntry(task_id=f"t{i}", task_spec_hash=f"h{i}"))
        path = tmp_path / "proof_chain.jsonl"
        chain.save(path)
        loaded = ProofChain.load(path)
        assert len(loaded) == 3
        assert loaded.verify() is True

    def test_no_temp_files_on_save(self, tmp_path: Path) -> None:
        chain = ProofChain()
        chain.append(ProofEntry(task_id="t1", task_spec_hash="h1"))
        chain.save(tmp_path / "chain.jsonl")
        assert not list(tmp_path.glob("*.tmp"))

    def test_chain_to_markdown(self) -> None:
        chain = ProofChain()
        chain.append(ProofEntry(task_id="t1", task_spec_hash="abc", model_version="gpt-4o"))
        md = chain.to_markdown()
        assert "t1" in md
        assert "gpt-4o" in md

    def test_hmac_signature(self) -> None:
        chain = ProofChain(signing_key="test-key-123")
        entry = ProofEntry(task_id="t1", task_spec_hash="abc")
        chain.append(entry)
        assert entry.chain_signature != ""
        assert len(entry.chain_signature) == 64  # HMAC-SHA256 hex
