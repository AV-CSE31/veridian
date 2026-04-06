"""
tests.unit.test_operator_replay
──────────────────────────────────
Unit tests for the operator replay differ.

Proves:
- Diff two replay snapshots (added/removed/changed tasks)
- Selective replay by task IDs
"""

from __future__ import annotations

from veridian.operator.replay import OperatorReplay, ReplayDiff

# ── Snapshots ────────────────────────────────────────────────────────────────


def _old_snapshot() -> dict[str, dict[str, object]]:
    return {
        "t-001": {"status": "completed", "result": "pass"},
        "t-002": {"status": "failed", "error": "timeout"},
        "t-003": {"status": "completed", "result": "pass"},
    }


def _new_snapshot() -> dict[str, dict[str, object]]:
    return {
        "t-001": {"status": "completed", "result": "pass"},  # unchanged
        "t-002": {"status": "completed", "result": "pass"},  # changed
        "t-004": {"status": "pending"},  # added
        # t-003 removed
    }


# ── Diff snapshots ──────────────────────────────────────────────────────────


class TestDiffSnapshots:
    def test_added_tasks(self) -> None:
        diff = OperatorReplay.diff_snapshots(_old_snapshot(), _new_snapshot())
        added_ids = [e["task_id"] for e in diff.added]
        assert "t-004" in added_ids

    def test_removed_tasks(self) -> None:
        diff = OperatorReplay.diff_snapshots(_old_snapshot(), _new_snapshot())
        removed_ids = [e["task_id"] for e in diff.removed]
        assert "t-003" in removed_ids

    def test_changed_tasks(self) -> None:
        diff = OperatorReplay.diff_snapshots(_old_snapshot(), _new_snapshot())
        changed_ids = [e["task_id"] for e in diff.changed]
        assert "t-002" in changed_ids

    def test_unchanged_not_in_diff(self) -> None:
        diff = OperatorReplay.diff_snapshots(_old_snapshot(), _new_snapshot())
        all_ids = (
            [e["task_id"] for e in diff.added]
            + [e["task_id"] for e in diff.removed]
            + [e["task_id"] for e in diff.changed]
        )
        assert "t-001" not in all_ids

    def test_identical_snapshots(self) -> None:
        snap = _old_snapshot()
        diff = OperatorReplay.diff_snapshots(snap, dict(snap))
        assert len(diff.added) == 0
        assert len(diff.removed) == 0
        assert len(diff.changed) == 0

    def test_empty_snapshots(self) -> None:
        diff = OperatorReplay.diff_snapshots({}, {})
        assert len(diff.added) == 0
        assert len(diff.removed) == 0
        assert len(diff.changed) == 0

    def test_diff_returns_replay_diff(self) -> None:
        diff = OperatorReplay.diff_snapshots(_old_snapshot(), _new_snapshot())
        assert isinstance(diff, ReplayDiff)


# ── Selective replay ─────────────────────────────────────────────────────────


class TestSelectiveReplay:
    def test_filters_by_task_ids(self) -> None:
        snapshot = _old_snapshot()
        result = OperatorReplay.selective_replay(snapshot, ["t-001", "t-003"])
        assert "t-001" in result
        assert "t-003" in result
        assert "t-002" not in result

    def test_missing_ids_excluded(self) -> None:
        snapshot = _old_snapshot()
        result = OperatorReplay.selective_replay(snapshot, ["t-001", "t-999"])
        assert "t-001" in result
        assert "t-999" not in result

    def test_empty_ids(self) -> None:
        snapshot = _old_snapshot()
        result = OperatorReplay.selective_replay(snapshot, [])
        assert len(result) == 0

    def test_returns_dict(self) -> None:
        snapshot = _old_snapshot()
        result = OperatorReplay.selective_replay(snapshot, ["t-001"])
        assert isinstance(result, dict)
