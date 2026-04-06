from __future__ import annotations

import pytest

import veridian.verify.builtin  # noqa: F401 - triggers verifier registration
from veridian.core.task import PRMBudget, PRMRunResult, TraceStep
from veridian.verify.base import registry
from veridian.verify.builtin.prm_reference import PRMReferenceVerifier


def _make_steps() -> list[TraceStep]:
    return [
        TraceStep(
            step_id="step-1",
            role="assistant",
            action_type="reason",
            content="We implemented the fix and verified the output is correct.",
            timestamp_ms=1,
        ),
        TraceStep(
            step_id="step-2",
            role="assistant",
            action_type="reason",
            content="Maybe there is still an error and we are not sure.",
            timestamp_ms=2,
        ),
    ]


def test_prm_reference_is_registered_and_instantiable() -> None:
    verifier = registry.get("prm_reference")
    assert isinstance(verifier, PRMReferenceVerifier)


def test_prm_reference_scoring_is_deterministic() -> None:
    verifier = PRMReferenceVerifier()
    steps = _make_steps()
    budget = PRMBudget(max_steps_per_call=10)

    first = verifier.score_steps(task_id="task-1", steps=steps, context={}, budget=budget)
    second = verifier.score_steps(task_id="task-1", steps=steps, context={}, budget=budget)

    assert first == second
    assert len(first.scored_steps) == 2
    assert first.scored_steps[0].score > first.scored_steps[1].score


def test_prm_reference_aggregate_and_policy_defaults() -> None:
    verifier = PRMReferenceVerifier()
    steps = _make_steps()

    result = verifier.score_steps(task_id="task-1", steps=steps, context={}, budget=PRMBudget())

    expected_average = round(
        sum(step.score for step in result.scored_steps) / len(result.scored_steps),
        3,
    )
    assert result.aggregate_score == pytest.approx(expected_average)
    assert result.aggregate_confidence == pytest.approx(
        round(sum(step.confidence for step in result.scored_steps) / len(result.scored_steps), 3)
    )
    assert result.policy_action == "allow"
    assert isinstance(result, PRMRunResult)
