"""
tests.test_consensus
─────────────────────
Tests for F3.2 — Multi-Model Consensus Verification.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from veridian.core.task import Task, TaskResult
from veridian.providers.base import LLMProvider, LLMResponse, Message
from veridian.verify.builtin.consensus import (
    AgreementStrategy,
    ConsensusResult,
    ConsensusVerifier,
    ModelVote,
)

# ─── helpers ──────────────────────────────────────────────────────────────────


def make_task(**kwargs) -> Task:
    return Task(title="test task", description="Do something", **kwargs)


def make_result(raw: str = "output") -> TaskResult:
    return TaskResult(raw_output=raw)


def make_provider(responses: list[str], model_name: str = "mock") -> LLMProvider:
    """Create a mock provider that returns scripted text responses."""
    mock = MagicMock(spec=LLMProvider)
    call_count = {"n": 0}

    def complete(messages: list[Message], **kwargs) -> LLMResponse:
        idx = call_count["n"] % len(responses)
        call_count["n"] += 1
        return LLMResponse(content=responses[idx], model=model_name)

    mock.complete.side_effect = complete
    return mock


# ─── ModelVote tests ──────────────────────────────────────────────────────────


class TestModelVote:
    def test_creation(self) -> None:
        vote = ModelVote(model_id="gpt-4", passed=True, confidence=0.9)
        assert vote.model_id == "gpt-4"
        assert vote.passed is True
        assert vote.confidence == 0.9

    def test_default_confidence(self) -> None:
        vote = ModelVote(model_id="claude", passed=False)
        assert vote.confidence == 1.0


# ─── ConsensusResult tests ────────────────────────────────────────────────────


class TestConsensusResult:
    def test_all_agree_pass(self) -> None:
        votes = [
            ModelVote(model_id="m1", passed=True),
            ModelVote(model_id="m2", passed=True),
            ModelVote(model_id="m3", passed=True),
        ]
        r = ConsensusResult(votes=votes, threshold=0.5)
        assert r.consensus_passed is True
        assert r.agreement_rate == 1.0

    def test_majority_pass(self) -> None:
        votes = [
            ModelVote(model_id="m1", passed=True),
            ModelVote(model_id="m2", passed=True),
            ModelVote(model_id="m3", passed=False),
        ]
        r = ConsensusResult(votes=votes, threshold=0.5)
        assert r.consensus_passed is True
        assert abs(r.agreement_rate - 2 / 3) < 1e-6

    def test_majority_fail(self) -> None:
        votes = [
            ModelVote(model_id="m1", passed=False),
            ModelVote(model_id="m2", passed=False),
            ModelVote(model_id="m3", passed=True),
        ]
        r = ConsensusResult(votes=votes, threshold=0.5)
        assert r.consensus_passed is False

    def test_disagreeing_models(self) -> None:
        votes = [
            ModelVote(model_id="m1", passed=True),
            ModelVote(model_id="m2", passed=False),
        ]
        r = ConsensusResult(votes=votes, threshold=0.9)
        assert r.consensus_passed is False
        disagreeing = r.disagreeing_models()
        assert len(disagreeing) >= 1

    def test_weighted_by_confidence(self) -> None:
        votes = [
            ModelVote(model_id="m1", passed=True, confidence=0.9),
            ModelVote(model_id="m2", passed=True, confidence=0.8),
            ModelVote(model_id="m3", passed=False, confidence=0.1),
        ]
        r = ConsensusResult(votes=votes, threshold=0.5, strategy=AgreementStrategy.WEIGHTED)
        assert r.consensus_passed is True


# ─── ConsensusVerifier tests ──────────────────────────────────────────────────


class TestConsensusVerifier:
    def test_requires_at_least_2_models(self) -> None:
        from veridian.core.exceptions import VeridianConfigError
        with pytest.raises(VeridianConfigError):
            ConsensusVerifier(providers=[make_provider(["pass"])])

    def test_max_5_models(self) -> None:
        from veridian.core.exceptions import VeridianConfigError
        providers = [make_provider(["pass"], f"m{i}") for i in range(6)]
        with pytest.raises(VeridianConfigError):
            ConsensusVerifier(providers=providers)

    def test_all_pass_gives_passed_true(self) -> None:
        providers = [
            make_provider(["PASS"], "m1"),
            make_provider(["PASS"], "m2"),
        ]
        v = ConsensusVerifier(providers=providers, prompt_template="Verify: {output}\nAnswer PASS or FAIL.")
        task = make_task()
        result = make_result("good output")
        vr = v.verify(task, result)
        assert vr.passed is True

    def test_all_fail_gives_passed_false(self) -> None:
        providers = [
            make_provider(["FAIL"], "m1"),
            make_provider(["FAIL"], "m2"),
        ]
        v = ConsensusVerifier(providers=providers, prompt_template="Verify: {output}\nAnswer PASS or FAIL.")
        task = make_task()
        result = make_result("bad output")
        vr = v.verify(task, result)
        assert vr.passed is False

    def test_error_message_mentions_disagreement(self) -> None:
        providers = [
            make_provider(["PASS"], "m1"),
            make_provider(["FAIL"], "m2"),
            make_provider(["FAIL"], "m3"),
        ]
        v = ConsensusVerifier(providers=providers, threshold=0.8)
        task = make_task()
        result = make_result()
        vr = v.verify(task, result)
        assert vr.passed is False
        assert vr.error is not None
        assert len(vr.error) <= 300

    def test_evidence_contains_votes(self) -> None:
        providers = [
            make_provider(["PASS"], "m1"),
            make_provider(["PASS"], "m2"),
        ]
        v = ConsensusVerifier(providers=providers)
        vr = v.verify(make_task(), make_result())
        assert "votes" in vr.evidence
        assert len(vr.evidence["votes"]) == 2

    def test_evidence_contains_agreement_rate(self) -> None:
        providers = [make_provider(["PASS"], f"m{i}") for i in range(3)]
        v = ConsensusVerifier(providers=providers)
        vr = v.verify(make_task(), make_result())
        assert "agreement_rate" in vr.evidence

    def test_configurable_threshold(self) -> None:
        # 2 pass, 1 fail → 66% agreement
        # threshold=0.7 → fail; threshold=0.5 → pass
        providers = [
            make_provider(["PASS"], "m1"),
            make_provider(["PASS"], "m2"),
            make_provider(["FAIL"], "m3"),
        ]
        v_strict = ConsensusVerifier(providers=providers, threshold=0.9)
        vr = v_strict.verify(make_task(), make_result())
        assert vr.passed is False

        providers2 = [
            make_provider(["PASS"], "m1"),
            make_provider(["PASS"], "m2"),
            make_provider(["FAIL"], "m3"),
        ]
        v_loose = ConsensusVerifier(providers=providers2, threshold=0.5)
        vr2 = v_loose.verify(make_task(), make_result())
        assert vr2.passed is True

    def test_verifier_id(self) -> None:
        providers = [make_provider(["PASS"]), make_provider(["PASS"])]
        v = ConsensusVerifier(providers=providers)
        assert v.id == "consensus"

    def test_majority_vote_strategy(self) -> None:
        providers = [
            make_provider(["PASS"], "m1"),
            make_provider(["PASS"], "m2"),
            make_provider(["FAIL"], "m3"),
        ]
        v = ConsensusVerifier(
            providers=providers,
            strategy=AgreementStrategy.MAJORITY,
            threshold=0.5,
        )
        vr = v.verify(make_task(), make_result())
        assert vr.passed is True
