"""
veridian.operator.approvals
──────────────────────────────
Operator approval queue — human-in-the-loop gating for sensitive actions.

Tasks requiring operator sign-off are queued here. Operators approve or reject
via the CLI or dashboard. Persistence uses atomic file writes (tempfile +
os.replace) consistent with the rest of Veridian.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veridian.core.exceptions import OperatorError

__all__ = [
    "ApprovalRequest",
    "ApprovalQueue",
]

log = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """A single approval request in the queue."""

    task_id: str
    reason: str
    requested_at: str  # ISO-8601
    status: str = "pending"  # "pending" | "approved" | "rejected"

    def to_dict(self) -> dict[str, str]:
        """Serialize to JSON-compatible dict."""
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRequest:
        """Deserialize from JSON-compatible dict."""
        return cls(
            task_id=str(data["task_id"]),
            reason=str(data["reason"]),
            requested_at=str(data["requested_at"]),
            status=str(data.get("status", "pending")),
        )


class ApprovalQueue:
    """Human-in-the-loop approval queue for operator gating.

    Pending requests are stored in a dict keyed by task_id. Approved/rejected
    requests are removed from the pending set. Persistence is optional via
    :meth:`save` / :meth:`load` using atomic writes.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}

    # ── Public API ───────────────────────────────────────────────────────────

    def add(self, task_id: str, reason: str) -> None:
        """Add a new pending approval request."""
        req = ApprovalRequest(
            task_id=task_id,
            reason=reason,
            requested_at=datetime.now(tz=UTC).isoformat(),
            status="pending",
        )
        self._pending[task_id] = req
        log.info("approval.added task_id=%s reason=%s", task_id, reason)

    def approve(self, task_id: str) -> None:
        """Approve a pending request. Raises OperatorError if not found."""
        if task_id not in self._pending:
            raise OperatorError(f"No pending approval for task {task_id!r}")
        req = self._pending.pop(task_id)
        req.status = "approved"
        log.info("approval.approved task_id=%s", task_id)

    def reject(self, task_id: str, reason: str) -> None:
        """Reject a pending request. Raises OperatorError if not found."""
        if task_id not in self._pending:
            raise OperatorError(f"No pending approval for task {task_id!r}")
        req = self._pending.pop(task_id)
        req.status = "rejected"
        log.info("approval.rejected task_id=%s reason=%s", task_id, reason)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all pending approval requests."""
        return list(self._pending.values())

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Persist the current queue to disk atomically (tempfile + os.replace)."""
        path = Path(path)
        data = {
            "version": 1,
            "pending": {tid: req.to_dict() for tid, req in self._pending.items()},
        }
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
            encoding="utf-8",
        ) as f:
            json.dump(data, f, indent=2)
            tmp_path = Path(f.name)
        os.replace(tmp_path, path)
        log.debug("approval.saved path=%s count=%d", path, len(self._pending))

    def load(self, path: str | Path) -> None:
        """Load queue state from disk."""
        path = Path(path)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._pending = {}
        for tid, req_dict in data.get("pending", {}).items():
            self._pending[tid] = ApprovalRequest.from_dict(req_dict)
        log.debug("approval.loaded path=%s count=%d", path, len(self._pending))
