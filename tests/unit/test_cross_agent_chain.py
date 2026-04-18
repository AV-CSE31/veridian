"""
Tests for veridian.observability.cross_agent_chain — Parent/child ProofChain linkage (WCP-012).
"""

from __future__ import annotations

import pytest

from veridian.observability.cross_agent_chain import (
    CrossAgentLink,
    CrossAgentLinkError,
    build_link,
    verify_link,
)
from veridian.observability.proof_chain import ProofChain, ProofEntry


def _entry(task_id: str, output_hash: str = "out") -> ProofEntry:
    return ProofEntry(
        task_id=task_id,
        task_spec_hash=f"spec-{task_id}",
        verifier_config_hash="vcfg",
        model_version="gemini/gemini-2.5-flash",
        input_hash=f"in-{task_id}",
        output_hash=output_hash,
        verification_evidence={"passed": True},
        policy_attestation=["safety_v1"],
        # Pin timestamp so chain hashes are stable across runs.
        timestamp="2026-04-18T00:00:00+00:00",
    )


def _populated_chain(task_ids: list[str]) -> ProofChain:
    chain = ProofChain()
    for tid in task_ids:
        chain.append(_entry(tid))
    return chain


# ── CrossAgentLink dataclass ───────────────────────────────────────────────


class TestCrossAgentLinkDataclass:
    def test_to_dict_roundtrip(self) -> None:
        link = CrossAgentLink(
            parent_task_id="parent_1",
            child_task_id="child_1",
            parent_tail_hash="ph",
            child_head_hash="ch",
            anchor_hash="ah",
            metadata={"reason": "delegation"},
        )
        d = link.to_dict()
        rebuilt = CrossAgentLink.from_dict(d)
        assert rebuilt == link

    def test_metadata_defaults_to_empty_dict(self) -> None:
        link = CrossAgentLink(
            parent_task_id="p",
            child_task_id="c",
            parent_tail_hash="ph",
            child_head_hash="ch",
            anchor_hash="ah",
        )
        assert link.metadata == {}


# ── build_link ─────────────────────────────────────────────────────────────


class TestBuildLink:
    def test_builds_link_with_anchor(self) -> None:
        parent = _populated_chain(["p1", "p2"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p2", child_task_id="c1")

        assert link.parent_task_id == "p2"
        assert link.child_task_id == "c1"
        assert link.parent_tail_hash == parent._entries[-1].compute_hash()
        assert link.child_head_hash == child._entries[0].compute_hash()
        assert len(link.anchor_hash) == 64  # SHA-256 hex digest

    def test_anchor_is_deterministic(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        a = build_link(parent, child, parent_task_id="p1", child_task_id="c1")
        b = build_link(parent, child, parent_task_id="p1", child_task_id="c1")
        assert a.anchor_hash == b.anchor_hash

    def test_different_task_ids_produce_different_anchors(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        a = build_link(parent, child, parent_task_id="p1", child_task_id="c1")
        b = build_link(parent, child, parent_task_id="p1", child_task_id="c2")
        assert a.anchor_hash != b.anchor_hash

    def test_metadata_preserved(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        link = build_link(
            parent, child, parent_task_id="p1", child_task_id="c1",
            metadata={"reason": "delegate-to-search-agent"},
        )
        assert link.metadata == {"reason": "delegate-to-search-agent"}

    def test_empty_parent_raises(self) -> None:
        empty = ProofChain()
        child = _populated_chain(["c1"])
        with pytest.raises(CrossAgentLinkError):
            build_link(empty, child, parent_task_id="p", child_task_id="c1")

    def test_empty_child_raises(self) -> None:
        parent = _populated_chain(["p1"])
        empty = ProofChain()
        with pytest.raises(CrossAgentLinkError):
            build_link(parent, empty, parent_task_id="p1", child_task_id="c")


# ── verify_link ────────────────────────────────────────────────────────────


class TestVerifyLink:
    def test_unmodified_link_verifies(self) -> None:
        parent = _populated_chain(["p1", "p2"])
        child = _populated_chain(["c1", "c2"])
        link = build_link(parent, child, parent_task_id="p2", child_task_id="c1")
        assert verify_link(link, parent_chain=parent, child_chain=child) is True

    def test_tampered_parent_tail_breaks_link(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p1", child_task_id="c1")

        # Append a new entry — parent tail hash now differs.
        parent.append(_entry("p2_injected"))
        assert verify_link(link, parent_chain=parent, child_chain=child) is False

    def test_substituted_child_breaks_link(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p1", child_task_id="c1")

        # Build a *different* child chain with the same task_id.
        substituted = ProofChain()
        substituted.append(_entry("c1", output_hash="DIFFERENT_OUTPUT"))
        assert verify_link(link, parent_chain=parent, child_chain=substituted) is False

    def test_tampered_anchor_hash_breaks_link(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p1", child_task_id="c1")

        # Hand-edit the anchor_hash by reconstructing the dataclass.
        tampered = CrossAgentLink(
            parent_task_id=link.parent_task_id,
            child_task_id=link.child_task_id,
            parent_tail_hash=link.parent_tail_hash,
            child_head_hash=link.child_head_hash,
            anchor_hash="0" * 64,
            created_at=link.created_at,
            metadata=dict(link.metadata),
        )
        assert verify_link(tampered, parent_chain=parent, child_chain=child) is False

    def test_empty_chain_after_link_returns_false(self) -> None:
        parent = _populated_chain(["p1"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p1", child_task_id="c1")
        # If a verifier is handed an empty chain, do not raise — just fail.
        assert verify_link(link, parent_chain=ProofChain(), child_chain=child) is False
        assert verify_link(link, parent_chain=parent, child_chain=ProofChain()) is False


class TestEndToEndFlow:
    def test_link_serialization_roundtrip_still_verifies(self) -> None:
        parent = _populated_chain(["p1", "p2"])
        child = _populated_chain(["c1"])
        link = build_link(parent, child, parent_task_id="p2", child_task_id="c1")

        # Serialize → deserialize (e.g., via JSON storage) and re-verify.
        d = link.to_dict()
        rebuilt = CrossAgentLink.from_dict(d)
        assert verify_link(rebuilt, parent_chain=parent, child_chain=child) is True
