"""
tests.unit.test_checkpoint_cursor
──────────────────────────────────
WCP-011: Unit tests for the canonical CheckpointCursor schema.

Covers:
- Construction, validation, dict round-trip.
- State-hash determinism.
- advance_cursor monotonicity guarantees.
- Cross-task cursor rejection.
- is_step_completed logic.
"""

from __future__ import annotations

import pytest

from veridian.core.task import TaskResult, TraceStep
from veridian.loop.checkpoint_cursor import (
    CheckpointCursor,
    CheckpointCursorError,
    advance_cursor,
    compute_state_hash,
    cursor_from_result,
    is_step_completed,
    load_cursor,
    write_cursor,
)


def _mk_result() -> TaskResult:
    return TaskResult(raw_output="")


def _mk_trace_step(step_id: str, idx: int = 0) -> TraceStep:
    return TraceStep(
        step_id=step_id,
        role="assistant",
        action_type="reason",
        content=f"step {idx}",
        timestamp_ms=idx,
    )


class TestCheckpointCursorValidation:
    def test_rejects_empty_task_id(self) -> None:
        with pytest.raises(CheckpointCursorError, match="task_id"):
            CheckpointCursor(task_id="", step_index=0, step_id="s1")

    def test_rejects_negative_step_index(self) -> None:
        with pytest.raises(CheckpointCursorError, match="step_index"):
            CheckpointCursor(task_id="t", step_index=-1, step_id="s1")

    def test_rejects_empty_step_id(self) -> None:
        with pytest.raises(CheckpointCursorError, match="step_id"):
            CheckpointCursor(task_id="t", step_index=0, step_id="")

    def test_accepts_minimal_valid_cursor(self) -> None:
        cursor = CheckpointCursor(task_id="t1", step_index=0, step_id="worker_turn_1")
        assert cursor.activity_key == ""
        assert cursor.state_hash == ""
        assert cursor.metadata == {}


class TestDictRoundTrip:
    def test_to_dict_and_from_dict_preserve_fields(self) -> None:
        cursor = CheckpointCursor(
            task_id="t1",
            step_index=3,
            step_id="verify",
            activity_key="llm_complete:t1:a0:t2",
            state_hash="abc123",
            timestamp_ms=1234567890000,
            metadata={"phase": "verification"},
        )
        restored = CheckpointCursor.from_dict(cursor.to_dict())
        assert restored == cursor

    def test_from_dict_coerces_types(self) -> None:
        cursor = CheckpointCursor.from_dict(
            {
                "task_id": "t1",
                "step_index": "5",  # string → int
                "step_id": "s",
                "timestamp_ms": "1000",
            }
        )
        assert cursor.step_index == 5
        assert cursor.timestamp_ms == 1000


class TestComputeStateHash:
    def test_empty_state_returns_empty_string(self) -> None:
        assert compute_state_hash(None) == ""
        assert compute_state_hash({}) == ""

    def test_identical_state_yields_identical_hash(self) -> None:
        a = compute_state_hash({"model": "mock/v1", "threshold": 0.72})
        b = compute_state_hash({"threshold": 0.72, "model": "mock/v1"})
        assert a == b  # order-independent

    def test_different_state_yields_different_hash(self) -> None:
        a = compute_state_hash({"model": "mock/v1"})
        b = compute_state_hash({"model": "mock/v2"})
        assert a != b

    def test_non_json_value_does_not_crash(self) -> None:
        # Functions / objects fall back to repr
        h = compute_state_hash({"fn": lambda x: x})
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest length


class TestLoadAndWrite:
    def test_load_returns_none_for_missing_cursor(self) -> None:
        result = _mk_result()
        assert load_cursor(result) is None

    def test_load_returns_none_for_none_result(self) -> None:
        assert load_cursor(None) is None

    def test_write_and_reload_round_trip(self) -> None:
        result = _mk_result()
        cursor = CheckpointCursor(task_id="t", step_index=2, step_id="s")
        write_cursor(result, cursor)
        reloaded = load_cursor(result)
        assert reloaded == cursor

    def test_load_raises_on_invalid_payload_type(self) -> None:
        result = _mk_result()
        result.extras["checkpoint_cursor"] = "not a dict"
        with pytest.raises(CheckpointCursorError, match="expected dict"):
            load_cursor(result)


class TestAdvanceCursor:
    def test_first_advance_produces_index_zero(self) -> None:
        result = _mk_result()
        cursor = advance_cursor(result=result, task_id="t1", step_id="s0")
        assert cursor.step_index == 0
        assert cursor.step_id == "s0"
        assert cursor.task_id == "t1"
        assert cursor.timestamp_ms > 0

    def test_subsequent_advance_increments_monotonically(self) -> None:
        result = _mk_result()
        c0 = advance_cursor(result=result, task_id="t1", step_id="s0")
        c1 = advance_cursor(result=result, task_id="t1", step_id="s1")
        c2 = advance_cursor(result=result, task_id="t1", step_id="s2")
        assert [c0.step_index, c1.step_index, c2.step_index] == [0, 1, 2]

    def test_advance_persists_activity_key_and_state_hash(self) -> None:
        result = _mk_result()
        cursor = advance_cursor(
            result=result,
            task_id="t1",
            step_id="s0",
            activity_key="llm_complete:t1:a0:t0",
            state={"model_id": "mock/v1"},
            metadata={"phase": "draft"},
        )
        assert cursor.activity_key == "llm_complete:t1:a0:t0"
        assert cursor.state_hash != ""
        assert cursor.metadata == {"phase": "draft"}

    def test_advance_rejects_cross_task_cursor(self) -> None:
        result = _mk_result()
        advance_cursor(result=result, task_id="t1", step_id="s0")
        with pytest.raises(CheckpointCursorError, match="task_id mismatch"):
            advance_cursor(result=result, task_id="t2", step_id="s1")

    def test_advance_stamps_result_extras(self) -> None:
        result = _mk_result()
        cursor = advance_cursor(result=result, task_id="t1", step_id="s0")
        assert result.extras.get("checkpoint_cursor") == cursor.to_dict()


class TestIsStepCompleted:
    def test_no_cursor_means_step_not_completed(self) -> None:
        result = _mk_result()
        result.trace_steps = [_mk_trace_step("a"), _mk_trace_step("b")]
        assert not is_step_completed(result, "a")

    def test_cursor_at_index_reports_step_complete(self) -> None:
        result = _mk_result()
        result.trace_steps = [
            _mk_trace_step("step_a", idx=0),
            _mk_trace_step("step_b", idx=1),
            _mk_trace_step("step_c", idx=2),
        ]
        # Advance cursor to step_b (index 1)
        advance_cursor(result=result, task_id="t", step_id="step_a")
        advance_cursor(result=result, task_id="t", step_id="step_b")
        assert is_step_completed(result, "step_a")
        assert is_step_completed(result, "step_b")
        # step_c is in trace_steps but cursor hasn't advanced to it yet
        assert not is_step_completed(result, "step_c")

    def test_unknown_step_id_returns_false(self) -> None:
        result = _mk_result()
        result.trace_steps = [_mk_trace_step("a")]
        advance_cursor(result=result, task_id="t", step_id="a")
        assert not is_step_completed(result, "nonexistent")


class TestCursorFromResultAlias:
    def test_alias_returns_same_as_load_cursor(self) -> None:
        result = _mk_result()
        advance_cursor(result=result, task_id="t", step_id="s")
        assert cursor_from_result(result) == load_cursor(result)
