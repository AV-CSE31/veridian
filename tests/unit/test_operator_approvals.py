"""
tests.unit.test_operator_approvals
─────────────────────────────────────
Unit tests for the operator approval queue.

Proves:
- Add pending approval, list pending, approve, reject
- Approve non-existent raises OperatorError
- Reject clears from pending queue
- Persistence via atomic file write
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.exceptions import OperatorError
from veridian.operator.approvals import ApprovalQueue

# ── Add / list ───────────────────────────────────────────────────────────────


class TestAddAndList:
    def test_add_creates_pending(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "dangerous action")
        pending = q.list_pending()
        assert len(pending) == 1
        assert pending[0].task_id == "t-001"
        assert pending[0].reason == "dangerous action"
        assert pending[0].status == "pending"

    def test_add_multiple(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "reason-a")
        q.add("t-002", "reason-b")
        assert len(q.list_pending()) == 2

    def test_add_populates_requested_at(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "reason")
        req = q.list_pending()[0]
        assert isinstance(req.requested_at, str)
        assert len(req.requested_at) > 0  # ISO timestamp


# ── Approve ──────────────────────────────────────────────────────────────────


class TestApprove:
    def test_approve_sets_status(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "needs approval")
        q.approve("t-001")
        # Approved tasks should no longer be in the pending list
        assert len(q.list_pending()) == 0

    def test_approve_nonexistent_raises_operator_error(self) -> None:
        q = ApprovalQueue()
        with pytest.raises(OperatorError, match="t-999"):
            q.approve("t-999")

    def test_approve_returns_nothing(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "reason")
        result = q.approve("t-001")
        assert result is None


# ── Reject ───────────────────────────────────────────────────────────────────


class TestReject:
    def test_reject_removes_from_pending(self) -> None:
        q = ApprovalQueue()
        q.add("t-001", "reason")
        q.reject("t-001", "not needed")
        assert len(q.list_pending()) == 0

    def test_reject_nonexistent_raises_operator_error(self) -> None:
        q = ApprovalQueue()
        with pytest.raises(OperatorError, match="t-999"):
            q.reject("t-999", "reason")


# ── Persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "approvals.json"
        q1 = ApprovalQueue()
        q1.add("t-001", "reason-a")
        q1.add("t-002", "reason-b")
        q1.save(path)

        q2 = ApprovalQueue()
        q2.load(path)
        pending = q2.list_pending()
        assert len(pending) == 2
        task_ids = {r.task_id for r in pending}
        assert task_ids == {"t-001", "t-002"}

    def test_save_uses_atomic_write(self, tmp_path: Path) -> None:
        """Verify file is created (atomic write produces the final file)."""
        path = tmp_path / "approvals.json"
        q = ApprovalQueue()
        q.add("t-001", "test")
        q.save(path)
        assert path.exists()

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "approvals.json"
        path.write_text("{}", encoding="utf-8")
        q = ApprovalQueue()
        q.load(path)
        assert len(q.list_pending()) == 0

    def test_save_after_approve(self, tmp_path: Path) -> None:
        path = tmp_path / "approvals.json"
        q1 = ApprovalQueue()
        q1.add("t-001", "reason")
        q1.approve("t-001")
        q1.save(path)

        q2 = ApprovalQueue()
        q2.load(path)
        assert len(q2.list_pending()) == 0
