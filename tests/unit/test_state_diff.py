"""Tests for StateDiffVerifier — verifies environment state, not just output."""

from __future__ import annotations

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.state_diff import StateDiffVerifier


def _task(tid: str = "t1") -> Task:
    return Task(id=tid, title="test", verifier_id="state_diff")


class TestCatchesHallucinatedSuccess:
    """Prove the 'hallucinated success' pattern is caught."""

    def test_detects_file_not_actually_deleted(self) -> None:
        """Agent says 'deleted 50 files' but 3 remain due to permission errors."""
        file_count = 3  # simulated: 3 files still exist after "deletion"
        verifier = StateDiffVerifier(
            capture_fn=lambda: {"file_count": file_count},
            expected_changes={"file_count": 0},
        )
        verifier.capture_pre_state()
        result = verifier.verify(_task(), TaskResult(raw_output="Deleted 50 files"))
        assert result.passed is False
        assert "file_count" in (result.error or "")

    def test_detects_row_count_mismatch(self) -> None:
        """Agent says 'migrated 1500 rows' but only 1200 arrived."""
        verifier = StateDiffVerifier(
            capture_fn=lambda: {"row_count": 1200},
            expected_changes={"row_count": 1500},
        )
        verifier.capture_pre_state()
        result = verifier.verify(_task(), TaskResult(raw_output="Migrated 1500 rows"))
        assert result.passed is False

    def test_detects_metadata_state_mismatch(self) -> None:
        """Fallback mode: check structured output against task metadata."""
        task = Task(
            id="t1",
            title="test",
            verifier_id="state_diff",
            metadata={"expected_state": {"status": "deleted"}},
        )
        result = TaskResult(raw_output="", structured={"status": "failed"})
        verifier = StateDiffVerifier()
        v = verifier.verify(task, result)
        assert v.passed is False
        assert "status" in (v.error or "")


class TestPassesRealStateChanges:
    """Prove legitimate state changes pass."""

    def test_passes_when_state_matches(self) -> None:
        verifier = StateDiffVerifier(
            capture_fn=lambda: {"file_count": 0},
            expected_changes={"file_count": 0},
        )
        verifier.capture_pre_state()
        result = verifier.verify(_task(), TaskResult(raw_output="All files deleted"))
        assert result.passed is True

    def test_passes_with_tolerance(self) -> None:
        verifier = StateDiffVerifier(
            capture_fn=lambda: {"temperature": 72.3},
            expected_changes={"temperature": 72.0},
            tolerance=0.5,
        )
        verifier.capture_pre_state()
        result = verifier.verify(_task(), TaskResult(raw_output="Set to 72"))
        assert result.passed is True

    def test_passes_without_capture_fn(self) -> None:
        """No capture fn = no state check = pass."""
        verifier = StateDiffVerifier()
        result = verifier.verify(_task(), TaskResult(raw_output="done"))
        assert result.passed is True

    def test_includes_timing_metadata(self) -> None:
        verifier = StateDiffVerifier(
            capture_fn=lambda: {"x": 1},
            expected_changes={"x": 1},
        )
        verifier.capture_pre_state()
        result = verifier.verify(_task(), TaskResult(raw_output=""))
        assert result.verification_ms is not None
        assert result.verification_ms >= 0
