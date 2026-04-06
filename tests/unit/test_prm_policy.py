from __future__ import annotations

import pytest

from veridian.contracts.prm_policy import (
    PRMPolicyConfig,
    evaluate_prm_policy,
)
from veridian.core.task import PRMRunResult, PRMScore


def make_result(score: float, confidence: float, passed: bool = True) -> PRMRunResult:
    return PRMRunResult(
        passed=passed,
        aggregate_score=score,
        aggregate_confidence=confidence,
        threshold=0.72,
        scored_steps=[
            PRMScore(
                step_id="step_1",
                score=score,
                confidence=confidence,
                model_id="prm-model",
                version="v1",
            )
        ],
        policy_action="allow",
    )


class TestPRMPolicyEvaluation:
    def test_allows_when_score_and_confidence_clear_thresholds(self) -> None:
        config = PRMPolicyConfig(threshold=0.72, min_confidence=0.65, strict_replay=True)
        decision = evaluate_prm_policy(make_result(0.91, 0.88), config)

        assert decision.action == "allow"
        assert decision.passed is True
        assert decision.repair_allowed is False
        assert decision.repairs_remaining == 1

    def test_warns_when_configured_and_not_strict(self) -> None:
        config = PRMPolicyConfig(
            threshold=0.8,
            min_confidence=0.4,
            action_below_threshold="warn",
            strict_replay=False,
        )
        decision = evaluate_prm_policy(make_result(0.5, 0.9), config)

        assert decision.action == "warn"
        assert decision.passed is False
        assert decision.repair_allowed is False

    def test_blocks_on_low_confidence_even_if_score_is_high(self) -> None:
        config = PRMPolicyConfig(
            threshold=0.7,
            min_confidence=0.8,
            action_below_confidence="block",
            strict_replay=True,
        )
        decision = evaluate_prm_policy(make_result(0.95, 0.5), config)

        assert decision.action == "block"
        assert decision.passed is False
        assert "confidence" in decision.reason

    def test_retry_with_repair_exposes_bounded_repair_budget(self) -> None:
        config = PRMPolicyConfig(
            threshold=0.9,
            min_confidence=0.5,
            action_below_threshold="retry_with_repair",
            max_repairs=2,
            strict_replay=True,
        )
        decision = evaluate_prm_policy(make_result(0.4, 0.6), config, repair_attempts_used=1)

        assert decision.action == "retry_with_repair"
        assert decision.repair_allowed is True
        assert decision.repairs_remaining == 1

    def test_retry_with_repair_falls_back_to_block_after_budget_is_exhausted(self) -> None:
        config = PRMPolicyConfig(
            threshold=0.9,
            min_confidence=0.5,
            action_below_threshold="retry_with_repair",
            max_repairs=1,
            strict_replay=True,
        )
        decision = evaluate_prm_policy(make_result(0.4, 0.6), config, repair_attempts_used=1)

        assert decision.action == "block"
        assert decision.repair_allowed is False
        assert decision.repairs_remaining == 0
        assert "repair budget exhausted" in decision.reason

    def test_missing_prm_result_fails_closed_by_default(self) -> None:
        decision = evaluate_prm_policy(None, PRMPolicyConfig(strict_replay=True))

        assert decision.action == "block"
        assert decision.passed is False
        assert "unavailable" in decision.reason

    def test_missing_prm_result_can_open_when_not_strict(self) -> None:
        decision = evaluate_prm_policy(None, PRMPolicyConfig(strict_replay=False))

        assert decision.action == "allow"
        assert decision.passed is True

    def test_invalid_config_values_raise(self) -> None:
        with pytest.raises(ValueError):
            PRMPolicyConfig(threshold=1.2)

        with pytest.raises(ValueError):
            PRMPolicyConfig(min_confidence=-0.1)

        with pytest.raises(ValueError):
            PRMPolicyConfig(max_repairs=-1)
