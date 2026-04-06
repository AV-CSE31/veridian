from __future__ import annotations

from pathlib import Path

from veridian.core.task import PRMRunResult, PRMScore, Task, TaskResult, TraceStep
from veridian.testing.recorder import AgentRecorder
from veridian.testing.replayer import ReplayAssertion, Replayer


def _build_record(
    *,
    run_id: str,
    task_id: str,
    policy_action: str,
    verification_passed: bool,
    aggregate_score: float,
    aggregate_confidence: float,
    error: str | None = None,
    repair_hint: str | None = None,
) -> tuple[Task, TaskResult]:
    task = Task(
        id=task_id,
        title=f"Replay case: {run_id}",
        description="Deterministic PRM replay cassette case",
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
        metadata={"prm": {"enabled": True, "strict_replay": True}},
    )
    trace_step = TraceStep(
        step_id=f"{task_id}_s1",
        role="assistant",
        action_type="reason",
        content="deterministic reasoning step",
        timestamp_ms=1,
        token_count=10,
    )
    score = PRMScore(
        step_id=trace_step.step_id,
        score=aggregate_score,
        confidence=aggregate_confidence,
        model_id="prm_reference",
        version="1",
        failure_mode=error,
    )
    prm_result = PRMRunResult(
        passed=verification_passed,
        aggregate_score=aggregate_score,
        aggregate_confidence=aggregate_confidence,
        threshold=0.72,
        scored_steps=[score],
        policy_action=policy_action,
        repair_hint=repair_hint,
        error=error,
    )
    result = TaskResult(
        raw_output="recorded output",
        structured={"summary": "done"},
        trace_steps=[trace_step],
        prm_result=prm_result,
        confidence={"composite": min(aggregate_score, aggregate_confidence)},
        extras={
            "prm_checkpoint": {
                "prm_scored_until_step_id": trace_step.step_id,
                "policy_action_log": [{"action": policy_action, "reason": error or ""}],
                "activity_invocation_ids": [f"prm:{task_id}"],
            }
        },
    )
    return task, result


def test_prm_record_replay_cassette_cases_are_deterministic(tmp_path: Path) -> None:
    recorder = AgentRecorder(trace_dir=tmp_path / "prm_cassettes")

    cases = [
        (
            "prm-normal",
            _build_record(
                run_id="prm-normal",
                task_id="t_prm_normal",
                policy_action="allow",
                verification_passed=True,
                aggregate_score=0.92,
                aggregate_confidence=0.91,
            ),
        ),
        (
            "prm-low-score",
            _build_record(
                run_id="prm-low-score",
                task_id="t_prm_low",
                policy_action="block",
                verification_passed=False,
                aggregate_score=0.31,
                aggregate_confidence=0.78,
                error="score_below_threshold",
            ),
        ),
        (
            "prm-repair",
            _build_record(
                run_id="prm-repair",
                task_id="t_prm_repair",
                policy_action="retry_with_repair",
                verification_passed=False,
                aggregate_score=0.44,
                aggregate_confidence=0.74,
                error="needs_repair",
                repair_hint="Improve reasoning trace and rescore.",
            ),
        ),
        (
            "prm-replay-incompatible",
            _build_record(
                run_id="prm-replay-incompatible",
                task_id="t_prm_incompatible",
                policy_action="block",
                verification_passed=False,
                aggregate_score=0.0,
                aggregate_confidence=0.0,
                error="PRM replay incompatible: model/version/prompt hash changed.",
            ),
        ),
    ]

    for run_id, (task, result) in cases:
        recorder.record(
            run_id=run_id,
            task=task,
            result=result,
            verification_passed=result.prm_result.passed if result.prm_result else False,
            verification_error=result.prm_result.error if result.prm_result else None,
        )

    replayer = Replayer(recorder=recorder)
    expected_actions = {
        "prm-normal": "allow",
        "prm-low-score": "block",
        "prm-repair": "retry_with_repair",
        "prm-replay-incompatible": "block",
    }
    replayer.add_assertion(
        ReplayAssertion(
            name="has_prm_result",
            check=lambda rec: rec.result.prm_result is not None,
        )
    )
    replayer.add_assertion(
        ReplayAssertion(
            name="policy_action_matches_case",
            check=lambda rec: (
                rec.result.prm_result is not None
                and rec.result.prm_result.policy_action == expected_actions[rec.run_id]
            ),
        )
    )
    replayer.add_assertion(
        ReplayAssertion(
            name="scored_step_ids_are_unique",
            check=lambda rec: (
                rec.result.prm_result is not None
                and len({s.step_id for s in rec.result.prm_result.scored_steps})
                == len(rec.result.prm_result.scored_steps)
            ),
        )
    )
    replayer.add_assertion(
        ReplayAssertion(
            name="replay_incompatible_case_is_blocked",
            check=lambda rec: (
                rec.run_id != "prm-replay-incompatible"
                or (
                    rec.result.prm_result is not None
                    and rec.result.prm_result.policy_action == "block"
                    and "replay incompatible" in (rec.result.prm_result.error or "").lower()
                )
            ),
        )
    )

    first = replayer.run()
    second = replayer.run()

    assert all(result.passed for result in first)
    assert [(r.assertion_name, r.run_id, r.passed) for r in first] == [
        (r.assertion_name, r.run_id, r.passed) for r in second
    ]


def test_prm_replay_catches_duplicate_scoring_regression(tmp_path: Path) -> None:
    recorder = AgentRecorder(trace_dir=tmp_path / "prm_cassettes_dupe")
    task, result = _build_record(
        run_id="prm-duplicate",
        task_id="t_prm_duplicate",
        policy_action="allow",
        verification_passed=True,
        aggregate_score=0.9,
        aggregate_confidence=0.9,
    )
    assert result.prm_result is not None
    duplicate = PRMScore(
        step_id=result.prm_result.scored_steps[0].step_id,
        score=0.9,
        confidence=0.9,
        model_id="prm_reference",
        version="1",
        failure_mode=None,
    )
    result.prm_result.scored_steps.append(duplicate)
    recorder.record(
        run_id="prm-duplicate",
        task=task,
        result=result,
        verification_passed=True,
    )

    replayer = Replayer(recorder=recorder)
    replayer.add_assertion(
        ReplayAssertion(
            name="scored_step_ids_are_unique",
            check=lambda rec: (
                rec.result.prm_result is not None
                and len({s.step_id for s in rec.result.prm_result.scored_steps})
                == len(rec.result.prm_result.scored_steps)
            ),
        )
    )
    replay_results = replayer.run()
    assert any(not item.passed for item in replay_results)
