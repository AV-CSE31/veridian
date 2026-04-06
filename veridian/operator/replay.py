"""
veridian.operator.replay
──────────────────────────
Operator replay diffing — compare two run snapshots and identify changes.

Supports full diff (added/removed/changed tasks) and selective replay where
operators choose a subset of task IDs to re-run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ReplayDiff",
    "OperatorReplay",
]

log = logging.getLogger(__name__)


@dataclass
class ReplayDiff:
    """Result of diffing two replay snapshots."""

    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)


class OperatorReplay:
    """Replay diffing and selective replay for operators.

    All methods are static/classmethod — no instance state needed.
    """

    @staticmethod
    def diff_snapshots(
        old: dict[str, dict[str, object]],
        new: dict[str, dict[str, object]],
    ) -> ReplayDiff:
        """Diff two snapshots and return added/removed/changed tasks.

        Args:
            old: Previous run snapshot keyed by task_id.
            new: Current run snapshot keyed by task_id.

        Returns:
            A ReplayDiff with lists of added, removed, and changed entries.
        """
        old_ids = set(old.keys())
        new_ids = set(new.keys())

        added = [{"task_id": tid, "details": dict(new[tid])} for tid in sorted(new_ids - old_ids)]
        removed = [{"task_id": tid, "details": dict(old[tid])} for tid in sorted(old_ids - new_ids)]
        changed: list[dict[str, Any]] = []
        for tid in sorted(old_ids & new_ids):
            if old[tid] != new[tid]:
                changed.append(
                    {
                        "task_id": tid,
                        "old": dict(old[tid]),
                        "new": dict(new[tid]),
                    }
                )

        return ReplayDiff(added=added, removed=removed, changed=changed)

    @staticmethod
    def selective_replay(
        snapshot: dict[str, dict[str, object]],
        task_ids: list[str],
    ) -> dict[str, dict[str, object]]:
        """Return a filtered snapshot containing only the specified task IDs.

        Task IDs not present in the snapshot are silently skipped.

        Args:
            snapshot: Full run snapshot keyed by task_id.
            task_ids: List of task IDs to include in the replay.

        Returns:
            Filtered snapshot dict.
        """
        return {tid: dict(snapshot[tid]) for tid in task_ids if tid in snapshot}
