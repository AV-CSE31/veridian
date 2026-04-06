"""
veridian.verify.builtin.consensus
──────────────────────────────────
ConsensusVerifier — run N models independently on the same task and
use statistical agreement to produce a final verification verdict.

Features:
- 2–5 models (configurable)
- Majority vote (default) or confidence-weighted strategies
- Configurable consensus threshold
- Disagreement analysis: which models disagree and on what
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.providers.base import LLMProvider, Message
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "You are a strict verification judge. "
    "Evaluate the following task output and respond with exactly one word: "
    "PASS if the output satisfies the task requirements, or FAIL if it does not.\n\n"
    "Task description: {description}\n\n"
    "Output to evaluate:\n{output}\n\n"
    "Verdict (PASS or FAIL):"
)


class AgreementStrategy(StrEnum):
    MAJORITY = "majority"
    WEIGHTED = "weighted"


@dataclass
class ModelVote:
    """A single model's verdict."""

    model_id: str
    passed: bool
    confidence: float = 1.0
    raw_response: str = ""
    error: str | None = None


@dataclass
class ConsensusResult:
    """
    Aggregated result from N model votes.

    agreement_rate: fraction of votes that match the majority.
    consensus_passed: True iff agreement_rate >= threshold.
    """

    votes: list[ModelVote]
    threshold: float = 0.5
    strategy: AgreementStrategy = AgreementStrategy.MAJORITY

    @property
    def agreement_rate(self) -> float:
        if not self.votes:
            return 0.0
        if self.strategy == AgreementStrategy.WEIGHTED:
            return self._weighted_agreement()
        return self._majority_agreement()

    def _majority_agreement(self) -> float:
        pass_count = sum(1 for v in self.votes if v.passed)
        return pass_count / len(self.votes)

    def _weighted_agreement(self) -> float:
        total_weight = sum(v.confidence for v in self.votes)
        if total_weight == 0:
            return 0.0
        pass_weight = sum(v.confidence for v in self.votes if v.passed)
        return pass_weight / total_weight

    @property
    def consensus_passed(self) -> bool:
        return self.agreement_rate >= self.threshold

    def disagreeing_models(self) -> list[ModelVote]:
        """
        Return votes that are in the minority.
        If consensus passed, returns models that voted FAIL.
        If consensus failed, returns models that voted PASS.
        """
        majority_is_pass = self.agreement_rate >= 0.5
        return [v for v in self.votes if v.passed != majority_is_pass]


def _parse_verdict(response: str) -> bool:
    """Extract PASS/FAIL from model response. Default to FAIL on ambiguity."""
    normalized = response.strip().upper()
    # Check for PASS anywhere in the response (first match wins)
    return "PASS" in normalized


class ConsensusVerifier(BaseVerifier):
    """
    Multi-model consensus verifier.

    Runs each provider independently on the same task, then aggregates
    verdicts using the configured strategy and threshold.

    Args:
        providers:         2–5 LLMProvider instances (one per model).
        threshold:         Minimum agreement rate to pass (default 0.5).
        strategy:          MAJORITY (default) or WEIGHTED by confidence.
        prompt_template:   Jinja-free format string with {description} and {output}.
    """

    id: ClassVar[str] = "consensus"
    description: ClassVar[str] = (
        "Run N models independently and require consensus agreement. "
        "Configurable threshold and majority/weighted strategies."
    )

    def __init__(
        self,
        providers: list[LLMProvider],
        threshold: float = 0.5,
        strategy: AgreementStrategy = AgreementStrategy.MAJORITY,
        prompt_template: str = _DEFAULT_PROMPT,
    ) -> None:
        if len(providers) < 2:
            raise VeridianConfigError(
                "ConsensusVerifier requires at least 2 providers. "
                f"Got {len(providers)}. Provide 2–5 LLMProvider instances."
            )
        if len(providers) > 5:
            raise VeridianConfigError(
                f"ConsensusVerifier supports at most 5 providers. "
                f"Got {len(providers)}. Use 2–5 models for practical consensus."
            )
        self.providers = providers
        self.threshold = threshold
        self.strategy = strategy
        self.prompt_template = prompt_template

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        votes: list[ModelVote] = []

        for i, provider in enumerate(self.providers):
            model_id = getattr(provider, "model", None) or f"model_{i}"
            try:
                prompt = self.prompt_template.format(
                    description=task.description or task.title,
                    output=result.raw_output[:2000],  # cap to avoid huge prompts
                )
                messages = [Message(role="user", content=prompt)]
                response = provider.complete(messages)
                raw = response.content
                model_id = response.model or model_id
                passed = _parse_verdict(raw)
                votes.append(
                    ModelVote(
                        model_id=model_id,
                        passed=passed,
                        raw_response=raw,
                    )
                )
            except Exception as exc:
                log.warning("consensus: provider %s error: %s", model_id, exc)
                votes.append(
                    ModelVote(
                        model_id=model_id,
                        passed=False,
                        error=str(exc),
                    )
                )

        consensus = ConsensusResult(
            votes=votes,
            threshold=self.threshold,
            strategy=self.strategy,
        )

        evidence: dict[str, Any] = {
            "votes": [
                {
                    "model_id": v.model_id,
                    "passed": v.passed,
                    "confidence": v.confidence,
                    "raw_response": v.raw_response[:200],
                }
                for v in votes
            ],
            "agreement_rate": round(consensus.agreement_rate, 4),
            "threshold": self.threshold,
            "strategy": self.strategy.value,
            "consensus_passed": consensus.consensus_passed,
        }

        if consensus.consensus_passed:
            return VerificationResult(passed=True, evidence=evidence)

        disagree = consensus.disagreeing_models()
        disagree_ids = ", ".join(v.model_id for v in disagree)
        rate_pct = int(consensus.agreement_rate * 100)
        error = (
            f"Consensus failed: {rate_pct}% agreement < {int(self.threshold * 100)}% threshold. "
            f"Disagreeing models: {disagree_ids}."
        )[:300]

        return VerificationResult(passed=False, error=error, evidence=evidence)


__all__ = [
    "ConsensusVerifier",
    "ModelVote",
    "ConsensusResult",
    "AgreementStrategy",
]
