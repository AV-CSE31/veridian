"""
veridian.contracts.prm_policy
──────────────────────────────
Deterministic PRM policy evaluation.

This module intentionally stays narrow: it turns PRM evidence into a runtime
action plus bounded repair metadata. Runner wiring happens elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from veridian.core.task import PRMRunResult

PRMPolicyAction = Literal["allow", "warn", "block", "retry_with_repair"]


@dataclass(frozen=True, slots=True)
class PRMPolicyConfig:
    """Configurable policy thresholds for PRM outcomes."""

    threshold: float = 0.72
    min_confidence: float = 0.65
    action_below_threshold: PRMPolicyAction = "retry_with_repair"
    action_below_confidence: PRMPolicyAction = "block"
    max_repairs: int = 1
    strict_replay: bool = True
    enabled: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between 0.0 and 1.0")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        if self.max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")


@dataclass(frozen=True, slots=True)
class PRMPolicyDecision:
    """Deterministic policy result returned to the runner."""

    action: PRMPolicyAction
    reason: str
    aggregate_score: float | None
    aggregate_confidence: float | None
    threshold: float
    min_confidence: float
    repair_attempts_used: int
    max_repairs: int
    repair_allowed: bool
    passed: bool

    @property
    def repairs_remaining(self) -> int:
        return max(0, self.max_repairs - self.repair_attempts_used)


def evaluate_prm_policy(
    prm_result: PRMRunResult | None,
    config: PRMPolicyConfig | None = None,
    *,
    repair_attempts_used: int = 0,
) -> PRMPolicyDecision:
    """
    Map PRM output to a deterministic runtime action.

    Fail-closed behavior:
    - Missing PRM result returns `block` when strict replay is enabled or PRM is disabled.
    - Confidence below the minimum threshold always overrides lower-priority allow/warn.
    """

    config = config or PRMPolicyConfig()
    if repair_attempts_used < 0:
        raise ValueError("repair_attempts_used must be >= 0")

    if not config.enabled:
        return PRMPolicyDecision(
            action="block" if config.strict_replay else "allow",
            reason="PRM policy disabled",
            aggregate_score=None,
            aggregate_confidence=None,
            threshold=config.threshold,
            min_confidence=config.min_confidence,
            repair_attempts_used=repair_attempts_used,
            max_repairs=config.max_repairs,
            repair_allowed=False,
            passed=not config.strict_replay,
        )

    if prm_result is None:
        return PRMPolicyDecision(
            action="block" if config.strict_replay else "allow",
            reason="PRM result unavailable",
            aggregate_score=None,
            aggregate_confidence=None,
            threshold=config.threshold,
            min_confidence=config.min_confidence,
            repair_attempts_used=repair_attempts_used,
            max_repairs=config.max_repairs,
            repair_allowed=False,
            passed=not config.strict_replay,
        )

    score = float(prm_result.aggregate_score)
    confidence = float(prm_result.aggregate_confidence)
    repairs_remaining = max(0, config.max_repairs - repair_attempts_used)

    if confidence < config.min_confidence:
        action = config.action_below_confidence
        return _build_decision(
            action=action,
            reason=(f"PRM confidence {confidence:.3f} below minimum {config.min_confidence:.3f}"),
            score=score,
            confidence=confidence,
            config=config,
            repair_attempts_used=repair_attempts_used,
            repairs_remaining=repairs_remaining,
            passed=score >= config.threshold and confidence >= config.min_confidence,
        )

    if score >= config.threshold:
        return PRMPolicyDecision(
            action="allow",
            reason=(f"PRM score {score:.3f} meets threshold {config.threshold:.3f}"),
            aggregate_score=score,
            aggregate_confidence=confidence,
            threshold=config.threshold,
            min_confidence=config.min_confidence,
            repair_attempts_used=repair_attempts_used,
            max_repairs=config.max_repairs,
            repair_allowed=False,
            passed=prm_result.passed,
        )

    action = config.action_below_threshold
    return _build_decision(
        action=action,
        reason=f"PRM score {score:.3f} below threshold {config.threshold:.3f}",
        score=score,
        confidence=confidence,
        config=config,
        repair_attempts_used=repair_attempts_used,
        repairs_remaining=repairs_remaining,
        passed=False,
    )


def _build_decision(
    *,
    action: PRMPolicyAction,
    reason: str,
    score: float,
    confidence: float,
    config: PRMPolicyConfig,
    repair_attempts_used: int,
    repairs_remaining: int,
    passed: bool,
) -> PRMPolicyDecision:
    if action == "retry_with_repair":
        if repairs_remaining <= 0:
            action = "block"
            reason = f"{reason}; repair budget exhausted"
            repair_allowed = False
            passed = False
        else:
            repair_allowed = True
    else:
        repair_allowed = False

    if config.strict_replay and action == "warn":
        action = "block"
        reason = f"{reason}; strict replay converts warn to block"
        passed = False
        repair_allowed = False

    return PRMPolicyDecision(
        action=action,
        reason=reason,
        aggregate_score=score,
        aggregate_confidence=confidence,
        threshold=config.threshold,
        min_confidence=config.min_confidence,
        repair_attempts_used=repair_attempts_used,
        max_repairs=config.max_repairs,
        repair_allowed=repair_allowed,
        passed=passed if action != "block" else False,
    )
