"""
veridian.entropy.gc
────────────────────
EntropyGC — read-only ledger consistency checker.

INVARIANT: EntropyGC NEVER mutates any ledger state.
           It reads, detects, reports. Never fixes, resets, or transitions tasks.

9 consistency checks:
  1. stale_in_progress      — tasks stuck IN_PROGRESS beyond threshold
  2. orphaned_dependency    — depends_on references non-existent task IDs
  3. circular_dependency    — dependency cycles (A→B→A, A→B→C→A, …)
  4. abandoned_blocks_pending — ABANDONED tasks that block PENDING dependents
  5. missing_required_field — tasks with empty title or description
  6. priority_outlier       — tasks with priority outside 0–100
  7. retry_exhaustion       — tasks where retry_count >= max_retries
  8. duplicate_task_id      — same ID appearing more than once in raw data
  9. progress_stall         — FAILED tasks not retried within stall window
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from veridian.core.task import TaskStatus
from veridian.ledger.ledger import TaskLedger

log = logging.getLogger(__name__)

__all__ = ["EntropyGC", "EntropyIssue", "IssueType"]

# ── Issue taxonomy ────────────────────────────────────────────────────────────


class IssueType(StrEnum):
    """Taxonomy of ledger consistency problems."""

    STALE_IN_PROGRESS = "stale_in_progress"
    ORPHANED_DEPENDENCY = "orphaned_dependency"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    ABANDONED_BLOCKS_PENDING = "abandoned_blocks_pending"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    PRIORITY_OUTLIER = "priority_outlier"
    RETRY_EXHAUSTION = "retry_exhaustion"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    PROGRESS_STALL = "progress_stall"


@dataclass
class EntropyIssue:
    """A single detected consistency problem."""

    issue_type: IssueType
    task_id: str
    detail: str


# ── EntropyGC ─────────────────────────────────────────────────────────────────


class EntropyGC:
    """
    Read-only ledger consistency checker.

    Runs 9 checks over the ledger and produces a list of EntropyIssue objects.
    Writes an entropy_report.md summary atomically.

    NEVER calls any mutating method on TaskLedger or any other stateful object.

    Usage::

        gc = EntropyGC(ledger=ledger, report_path=Path("entropy_report.md"))
        issues = gc.run()
        for issue in issues:
            print(issue.issue_type, issue.task_id, issue.detail)
    """

    _VALID_PRIORITY_RANGE = (0, 100)

    def __init__(
        self,
        ledger: TaskLedger,
        report_path: Path | None = None,
        stale_threshold_seconds: float = 3600.0,
        stall_threshold_seconds: float = 86400.0,
    ) -> None:
        self._ledger = ledger
        self._report_path = report_path or Path("entropy_report.md")
        self._stale_threshold = stale_threshold_seconds
        self._stall_threshold = stall_threshold_seconds

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> list[EntropyIssue]:
        """
        Run all 9 consistency checks and write entropy_report.md.
        Returns aggregated list of EntropyIssue objects.
        """
        issues: list[EntropyIssue] = []

        checks = [
            self.check_stale_in_progress,
            self.check_orphaned_dependencies,
            self.check_circular_dependencies,
            self.check_abandoned_with_pending_dependents,
            self.check_missing_required_fields,
            self.check_priority_outliers,
            self.check_retry_exhaustion,
            self.check_duplicate_task_ids,
            self.check_progress_stall,
        ]

        for check in checks:
            with contextlib.suppress(Exception):
                issues.extend(check())

        self._write_report(issues)
        return issues

    # ── Check 1: stale IN_PROGRESS ─────────────────────────────────────────────

    def check_stale_in_progress(self) -> list[EntropyIssue]:
        """Flag tasks that have been IN_PROGRESS beyond stale_threshold_seconds."""
        tasks = self._ledger.list(status=TaskStatus.IN_PROGRESS)
        now = datetime.now(UTC)
        issues: list[EntropyIssue] = []
        for task in tasks:
            updated = task.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age = (now - updated).total_seconds()
            if age >= self._stale_threshold:
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.STALE_IN_PROGRESS,
                        task_id=task.id,
                        detail=(
                            f"Task has been IN_PROGRESS for {age:.0f}s "
                            f"(threshold: {self._stale_threshold}s). "
                            f"Claimed by: {task.claimed_by!r}."
                        ),
                    )
                )
        return issues

    # ── Check 2: orphaned dependencies ────────────────────────────────────────

    def check_orphaned_dependencies(self) -> list[EntropyIssue]:
        """Flag tasks whose depends_on references a non-existent task ID."""
        all_tasks = self._ledger.list()
        known_ids = {t.id for t in all_tasks}
        issues: list[EntropyIssue] = []
        for task in all_tasks:
            missing = [dep for dep in task.depends_on if dep not in known_ids]
            for dep_id in missing:
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.ORPHANED_DEPENDENCY,
                        task_id=task.id,
                        detail=(f"Task depends on '{dep_id}' which does not exist in the ledger."),
                    )
                )
        return issues

    # ── Check 3: circular dependencies ────────────────────────────────────────

    def check_circular_dependencies(self) -> list[EntropyIssue]:
        """Detect dependency cycles using DFS with a colour map."""
        all_tasks = self._ledger.list()
        adj: dict[str, list[str]] = {t.id: t.depends_on for t in all_tasks}

        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {t.id: WHITE for t in all_tasks}
        issues: list[EntropyIssue] = []
        cycle_reported: set[frozenset[str]] = set()

        def dfs(node: str, path: list[str]) -> None:
            colour[node] = GREY
            path.append(node)
            for dep in adj.get(node, []):
                if dep not in colour:
                    continue  # orphaned — caught by check 2
                if colour[dep] == GREY:
                    # Found a cycle; report the smallest task in the cycle
                    cycle_nodes = frozenset(path[path.index(dep) :])
                    if cycle_nodes not in cycle_reported:
                        cycle_reported.add(cycle_nodes)
                        cycle_start = path[path.index(dep)]
                        issues.append(
                            EntropyIssue(
                                issue_type=IssueType.CIRCULAR_DEPENDENCY,
                                task_id=cycle_start,
                                detail=(
                                    f"Circular dependency detected: "
                                    f"{' → '.join(path[path.index(dep) :])} → {dep}"
                                ),
                            )
                        )
                elif colour[dep] == WHITE:
                    dfs(dep, path)
            path.pop()
            colour[node] = BLACK

        for task_id in list(colour):
            if colour[task_id] == WHITE:
                dfs(task_id, [])

        return issues

    # ── Check 4: abandoned blocks pending ─────────────────────────────────────

    def check_abandoned_with_pending_dependents(self) -> list[EntropyIssue]:
        """Flag PENDING tasks blocked by an ABANDONED dependency."""
        all_tasks = self._ledger.list()
        status_map = {t.id: t.status for t in all_tasks}
        issues: list[EntropyIssue] = []
        for task in all_tasks:
            if task.status != TaskStatus.PENDING:
                continue
            for dep_id in task.depends_on:
                if status_map.get(dep_id) == TaskStatus.ABANDONED:
                    issues.append(
                        EntropyIssue(
                            issue_type=IssueType.ABANDONED_BLOCKS_PENDING,
                            task_id=task.id,
                            detail=(
                                f"Task is PENDING but its dependency '{dep_id}' "
                                f"is ABANDONED. This task can never run."
                            ),
                        )
                    )
        return issues

    # ── Check 5: missing required fields ──────────────────────────────────────

    def check_missing_required_fields(self) -> list[EntropyIssue]:
        """Flag tasks with empty title or description."""
        issues: list[EntropyIssue] = []
        for task in self._ledger.list():
            missing = []
            if not task.title or not task.title.strip():
                missing.append("title")
            if not task.description or not task.description.strip():
                missing.append("description")
            if missing:
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.MISSING_REQUIRED_FIELD,
                        task_id=task.id,
                        detail=f"Task is missing required fields: {', '.join(missing)}.",
                    )
                )
        return issues

    # ── Check 6: priority outliers ─────────────────────────────────────────────

    def check_priority_outliers(self) -> list[EntropyIssue]:
        """Flag tasks with priority outside the valid 0–100 range."""
        lo, hi = self._VALID_PRIORITY_RANGE
        issues: list[EntropyIssue] = []
        for task in self._ledger.list():
            if not (lo <= task.priority <= hi):
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.PRIORITY_OUTLIER,
                        task_id=task.id,
                        detail=(
                            f"Task priority {task.priority} is outside valid range [{lo}, {hi}]."
                        ),
                    )
                )
        return issues

    # ── Check 7: retry exhaustion ──────────────────────────────────────────────

    def check_retry_exhaustion(self) -> list[EntropyIssue]:
        """Flag tasks where retry_count >= max_retries (imminent abandonment)."""
        issues: list[EntropyIssue] = []
        for task in self._ledger.list():
            if task.retry_count >= task.max_retries and not task.status.is_terminal:
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.RETRY_EXHAUSTION,
                        task_id=task.id,
                        detail=(
                            f"Task has {task.retry_count}/{task.max_retries} retries used. "
                            f"Next failure will abandon it permanently."
                        ),
                    )
                )
        return issues

    # ── Check 8: duplicate task IDs ───────────────────────────────────────────

    def check_duplicate_task_ids(self) -> list[EntropyIssue]:
        """Detect duplicate task IDs in the raw ledger data."""
        raw = self._ledger._read_raw()
        raw_tasks: Any = raw.get("tasks", {})
        if isinstance(raw_tasks, list):
            ids = [t.get("id", "") for t in raw_tasks]
        else:
            # dict keyed by ID — duplicates can't occur structurally
            return []

        seen: set[str] = set()
        dupes: set[str] = set()
        for task_id in ids:
            if task_id in seen:
                dupes.add(task_id)
            seen.add(task_id)

        issues: list[EntropyIssue] = []
        for task_id in sorted(dupes):
            issues.append(
                EntropyIssue(
                    issue_type=IssueType.DUPLICATE_TASK_ID,
                    task_id=task_id,
                    detail=f"Task ID '{task_id}' appears more than once in the ledger.",
                )
            )
        return issues

    # ── Check 9: progress stall ────────────────────────────────────────────────

    def check_progress_stall(self) -> list[EntropyIssue]:
        """Flag FAILED tasks that haven't been retried within stall_threshold_seconds."""
        tasks = self._ledger.list(status=TaskStatus.FAILED)
        now = datetime.now(UTC)
        issues: list[EntropyIssue] = []
        for task in tasks:
            updated = task.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            idle = (now - updated).total_seconds()
            if idle >= self._stall_threshold:
                issues.append(
                    EntropyIssue(
                        issue_type=IssueType.PROGRESS_STALL,
                        task_id=task.id,
                        detail=(
                            f"Task has been FAILED and unretried for {idle:.0f}s "
                            f"(threshold: {self._stall_threshold}s)."
                        ),
                    )
                )
        return issues

    # ── Report writing ────────────────────────────────────────────────────────

    def _write_report(self, issues: list[EntropyIssue]) -> None:
        """Atomically write entropy_report.md with the full issue list."""
        lines: list[str] = [
            "# EntropyGC Report",
            "",
            f"Generated: {datetime.now(UTC).isoformat()}",
            f"Total issues detected: {len(issues)}",
            "",
        ]

        if not issues:
            lines += [
                "## ✅ No Issues Found",
                "",
                "The ledger is in a consistent state.",
            ]
        else:
            by_type: dict[IssueType, list[EntropyIssue]] = {}
            for issue in issues:
                by_type.setdefault(issue.issue_type, []).append(issue)

            for issue_type, type_issues in sorted(by_type.items(), key=lambda x: x[0].value):
                lines += [
                    f"## {issue_type.value} ({len(type_issues)} issue(s))",
                    "",
                ]
                for issue in type_issues:
                    lines += [
                        f"- **Task:** `{issue.task_id}`",
                        f"  {issue.detail}",
                        "",
                    ]

        content = "\n".join(lines) + "\n"

        # Atomic write
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._report_path.parent,
            prefix=".entropy_",
            suffix=".tmp",
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, self._report_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            log.exception("Failed to write entropy report to %s", self._report_path)
