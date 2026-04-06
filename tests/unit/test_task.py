"""
tests.unit.test_task
─────────────────────
Unit tests for Task, TaskStatus state machine, TaskResult, LedgerStats.
"""

from datetime import datetime

import pytest

from veridian.core.task import (
    LedgerStats,
    PRMBudget,
    PRMRunResult,
    PRMScore,
    Task,
    TaskPriority,
    TaskResult,
    TaskStatus,
    TraceStep,
)

# ── TaskStatus state machine ──────────────────────────────────────────────────


class TestTaskStatusTransitions:
    def test_pending_to_in_progress(self):
        assert TaskStatus.PENDING.can_transition_to(TaskStatus.IN_PROGRESS)

    def test_pending_to_skipped(self):
        assert TaskStatus.PENDING.can_transition_to(TaskStatus.SKIPPED)

    def test_in_progress_to_verifying(self):
        assert TaskStatus.IN_PROGRESS.can_transition_to(TaskStatus.VERIFYING)

    def test_in_progress_to_failed(self):
        assert TaskStatus.IN_PROGRESS.can_transition_to(TaskStatus.FAILED)

    def test_in_progress_to_pending_crash_reset(self):
        # Crash recovery path
        assert TaskStatus.IN_PROGRESS.can_transition_to(TaskStatus.PENDING)

    def test_verifying_to_done(self):
        assert TaskStatus.VERIFYING.can_transition_to(TaskStatus.DONE)

    def test_verifying_to_failed(self):
        assert TaskStatus.VERIFYING.can_transition_to(TaskStatus.FAILED)

    def test_failed_to_pending_retry(self):
        assert TaskStatus.FAILED.can_transition_to(TaskStatus.PENDING)

    def test_failed_to_abandoned(self):
        assert TaskStatus.FAILED.can_transition_to(TaskStatus.ABANDONED)

    # ── Invalid transitions ───────────────────────────────────────────────────

    def test_done_is_terminal(self):
        assert not TaskStatus.DONE.can_transition_to(TaskStatus.PENDING)
        assert not TaskStatus.DONE.can_transition_to(TaskStatus.IN_PROGRESS)
        assert not TaskStatus.DONE.can_transition_to(TaskStatus.FAILED)

    def test_abandoned_is_terminal(self):
        assert not TaskStatus.ABANDONED.can_transition_to(TaskStatus.PENDING)

    def test_skipped_is_terminal(self):
        assert not TaskStatus.SKIPPED.can_transition_to(TaskStatus.PENDING)

    def test_pending_cannot_skip_to_done(self):
        assert not TaskStatus.PENDING.can_transition_to(TaskStatus.DONE)

    def test_is_terminal_flags(self):
        assert TaskStatus.DONE.is_terminal
        assert TaskStatus.ABANDONED.is_terminal
        assert TaskStatus.SKIPPED.is_terminal
        assert not TaskStatus.PENDING.is_terminal
        assert not TaskStatus.IN_PROGRESS.is_terminal
        assert not TaskStatus.FAILED.is_terminal


# ── Task dataclass ────────────────────────────────────────────────────────────


class TestTask:
    def test_auto_id_generation(self):
        t1 = Task(title="a")
        t2 = Task(title="b")
        assert t1.id != t2.id
        assert len(t1.id) == 12

    def test_default_status_is_pending(self):
        t = Task(title="test")
        assert t.status == TaskStatus.PENDING

    def test_default_priority(self):
        t = Task(title="test")
        assert t.priority == TaskPriority.NORMAL

    def test_depends_on_default_empty(self):
        t = Task(title="test")
        assert t.depends_on == []

    def test_is_terminal_delegates_to_status(self):
        t = Task(title="test", status=TaskStatus.DONE)
        assert t.is_terminal()
        t2 = Task(title="test", status=TaskStatus.PENDING)
        assert not t2.is_terminal()

    def test_to_dict_roundtrip(self):
        original = Task(
            title="Run migration",
            description="Migrate auth.py to Python 3.11",
            priority=TaskPriority.HIGH,
            phase="migration",
            depends_on=["abc123"],
            verifier_id="bash_exit",
            verifier_config={"command": "pytest tests/test_auth.py"},
            max_retries=5,
            metadata={"source_file": "src/auth.py"},
        )
        restored = Task.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.phase == original.phase
        assert restored.depends_on == original.depends_on
        assert restored.verifier_id == original.verifier_id
        assert restored.verifier_config == original.verifier_config
        assert restored.metadata == original.metadata

    def test_to_dict_with_result(self):
        t = Task(title="test")
        t.result = TaskResult(
            raw_output="<harness:result>{}</harness:result>",
            structured={"answer": 42},
            verified=True,
        )
        d = t.to_dict()
        assert d["result"]["structured"]["answer"] == 42
        assert d["result"]["verified"] is True

    def test_repr_is_reasonable(self):
        t = Task(title="Verify audit trail", status=TaskStatus.IN_PROGRESS)
        r = repr(t)
        assert "Verify audit trail" in r
        assert "in_progress" in r


# ── TaskResult ────────────────────────────────────────────────────────────────


class TestTaskResult:
    def test_roundtrip_empty(self):
        r = TaskResult(raw_output="some output")
        restored = TaskResult.from_dict(r.to_dict())
        assert restored.raw_output == "some output"
        assert restored.structured == {}
        assert restored.verified is False

    def test_roundtrip_full(self):
        r = TaskResult(
            raw_output="text",
            structured={"field": "value", "score": 0.95},
            artifacts=["output/report.json"],
            bash_outputs=[{"cmd": "pytest", "exit_code": 0}],
            trace_steps=[
                TraceStep(
                    step_id="s1",
                    role="assistant",
                    action_type="reason",
                    content="thinking",
                    timestamp_ms=1000,
                )
            ],
            prm_result=PRMRunResult(
                passed=True,
                aggregate_score=0.9,
                aggregate_confidence=0.8,
                threshold=0.7,
                scored_steps=[
                    PRMScore(
                        step_id="s1",
                        score=0.9,
                        confidence=0.8,
                        model_id="prm-model",
                        version="1",
                    )
                ],
                policy_action="allow",
            ),
            confidence={"composite": 0.88, "tier": "HIGH"},
            verifier_score=0.91,
            tool_calls=[{"name": "search_docs"}],
            timing={"worker_ms": 123.4, "verification_ms": 5.6},
            verification_evidence={"required_fields_present": True},
            verified=True,
            verification_error=None,
            token_usage={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
        )
        r.verified_at = datetime(2025, 1, 1, 12, 0, 0)
        d = r.to_dict()
        restored = TaskResult.from_dict(d)
        assert restored.structured["score"] == 0.95
        assert restored.artifacts == ["output/report.json"]
        assert restored.verified is True
        assert restored.token_usage["total_tokens"] == 1200
        assert restored.trace_steps[0].step_id == "s1"
        assert restored.prm_result is not None
        assert restored.prm_result.policy_action == "allow"
        assert restored.prm_result.scored_steps[0].model_id == "prm-model"
        assert restored.confidence is not None
        assert restored.confidence["composite"] == pytest.approx(0.88)
        assert restored.verifier_score == pytest.approx(0.91)
        assert restored.tool_calls[0]["name"] == "search_docs"
        assert restored.timing["verification_ms"] == pytest.approx(5.6)
        assert restored.verification_evidence["required_fields_present"] is True
        assert restored.verified_at.year == 2025

    def test_roundtrip_preserves_unknown_fields(self):
        payload = {
            "raw_output": "x",
            "structured": {},
            "future_field": {"x": 1},
            "new_metric": 42,
        }
        restored = TaskResult.from_dict(payload)
        out = restored.to_dict()
        assert out["future_field"] == {"x": 1}
        assert out["new_metric"] == 42


class TestPRMContracts:
    def test_prm_budget_roundtrip(self):
        b = PRMBudget(max_steps_per_call=10, max_tokens_per_call=500, max_latency_ms=1000)
        assert PRMBudget.from_dict(b.to_dict()) == b


# ── LedgerStats ───────────────────────────────────────────────────────────────


class TestLedgerStats:
    def test_convenience_properties(self):
        s = LedgerStats(
            total=10,
            by_status={"done": 4, "pending": 3, "failed": 2, "in_progress": 1},
        )
        assert s.done == 4
        assert s.pending == 3
        assert s.failed == 2
        assert s.in_progress == 1

    def test_pct_complete(self):
        s = LedgerStats(
            total=10,
            by_status={"done": 7, "skipped": 1, "pending": 2},
        )
        assert s.pct_complete == pytest.approx(0.8)

    def test_pct_complete_zero_total(self):
        s = LedgerStats(total=0)
        assert s.pct_complete == 0.0
