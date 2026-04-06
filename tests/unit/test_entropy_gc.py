"""
tests/unit/test_entropy_gc.py
──────────────────────────────
Tests for EntropyGC — read-only consistency checker for the task ledger.

TDD: these tests are written BEFORE the implementation.

INVARIANT: EntropyGC NEVER mutates any ledger state. Every test asserts this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from veridian.core.task import Task, TaskResult
from veridian.entropy.gc import EntropyGC, EntropyIssue, IssueType
from veridian.ledger.ledger import TaskLedger

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_ledger(tmp_path: Path, tasks: list[Task]) -> TaskLedger:
    ledger = TaskLedger(path=tmp_path / "ledger.json")
    if tasks:
        ledger.add(tasks)
    return ledger


def snapshot_tasks(ledger: TaskLedger) -> dict[str, str]:
    """Return {task_id: status} for all tasks — used to verify ledger is unchanged."""
    return {t.id: t.status.value for t in ledger.list()}


# ── EntropyIssue dataclass ────────────────────────────────────────────────────


class TestEntropyIssue:
    def test_entropy_issue_has_required_fields(self) -> None:
        """EntropyIssue must carry type, task_id, and detail."""
        issue = EntropyIssue(
            issue_type=IssueType.STALE_IN_PROGRESS,
            task_id="t1",
            detail="Task stuck for 3600 seconds",
        )
        assert issue.issue_type == IssueType.STALE_IN_PROGRESS
        assert issue.task_id == "t1"
        assert "3600" in issue.detail

    def test_issue_type_enum_has_all_9_types(self) -> None:
        """IssueType enum must have exactly 9 issue types."""
        assert len(IssueType) == 9


# ── EntropyGC constructor ─────────────────────────────────────────────────────


class TestEntropyGCConstructor:
    def test_constructs_with_ledger_and_report_path(self, tmp_path: Path) -> None:
        """EntropyGC should accept a ledger and an optional report_path."""
        ledger = make_ledger(tmp_path, [])
        gc = EntropyGC(ledger=ledger, report_path=tmp_path / "entropy_report.md")
        assert gc is not None

    def test_constructs_with_ledger_only(self, tmp_path: Path) -> None:
        """EntropyGC should work without a report_path (defaults to cwd)."""
        ledger = make_ledger(tmp_path, [])
        gc = EntropyGC(ledger=ledger)
        assert gc is not None


# ── Check 1: stale in_progress ─────────────────────────────────────────────


class TestCheckStaleInProgress:
    def test_detects_stale_in_progress_task(self, tmp_path: Path) -> None:
        """Should flag tasks that have been IN_PROGRESS beyond the stale threshold."""
        ledger = make_ledger(tmp_path, [Task(id="t1", title="Stale")])
        # Manually claim without resetting (simulates crash)
        ledger.claim("t1", runner_id="crashed-run")
        # Force the updated_at to look old
        task = ledger.get("t1")
        from datetime import UTC, datetime, timedelta

        task.updated_at = datetime.now(tz=UTC) - timedelta(hours=2)
        data = ledger._read_raw()  # type: ignore[attr-defined]
        data["tasks"][task.id]["updated_at"] = task.updated_at.isoformat()
        ledger._write_raw(data)  # type: ignore[attr-defined]

        gc = EntropyGC(ledger=ledger, stale_threshold_seconds=60)
        issues = gc.check_stale_in_progress()

        assert any(i.task_id == "t1" for i in issues)
        assert all(i.issue_type == IssueType.STALE_IN_PROGRESS for i in issues)

    def test_no_false_positive_for_recent_in_progress(self, tmp_path: Path) -> None:
        """A task claimed moments ago must NOT be flagged as stale."""
        ledger = make_ledger(tmp_path, [Task(id="t1", title="Recent")])
        ledger.claim("t1", runner_id="active-run")

        gc = EntropyGC(ledger=ledger, stale_threshold_seconds=3600)
        issues = gc.check_stale_in_progress()
        assert not any(i.task_id == "t1" for i in issues)

    def test_stale_check_never_mutates_ledger(self, tmp_path: Path) -> None:
        """check_stale_in_progress must not change any task status."""
        ledger = make_ledger(tmp_path, [Task(id="t1", title="Stale")])
        ledger.claim("t1", runner_id="old-run")
        task = ledger.get("t1")
        from datetime import UTC, datetime, timedelta

        task.updated_at = datetime.now(tz=UTC) - timedelta(hours=2)
        data = ledger._read_raw()  # type: ignore[attr-defined]
        data["tasks"][task.id]["updated_at"] = task.updated_at.isoformat()
        ledger._write_raw(data)  # type: ignore[attr-defined]

        before = snapshot_tasks(ledger)
        gc = EntropyGC(ledger=ledger, stale_threshold_seconds=60)
        gc.check_stale_in_progress()
        after = snapshot_tasks(ledger)

        assert before == after, "EntropyGC mutated ledger — forbidden!"


# ── Check 2: orphaned dependencies ────────────────────────────────────────────


class TestCheckOrphanedDependencies:
    def test_detects_orphaned_dependency(self, tmp_path: Path) -> None:
        """Should flag tasks whose depends_on references a non-existent task ID."""
        t = Task(id="t1", title="Orphan", depends_on=["ghost-id"])
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_orphaned_dependencies()

        assert any(i.task_id == "t1" for i in issues)
        assert any(i.issue_type == IssueType.ORPHANED_DEPENDENCY for i in issues)

    def test_no_false_positive_for_valid_dependency(self, tmp_path: Path) -> None:
        """A task depending on an existing task must NOT be flagged."""
        blocker = Task(id="blocker", title="Blocker")
        dep = Task(id="dep", title="Dep", depends_on=["blocker"])
        ledger = make_ledger(tmp_path, [blocker, dep])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_orphaned_dependencies()
        assert not any(i.task_id == "dep" for i in issues)

    def test_orphan_check_never_mutates_ledger(self, tmp_path: Path) -> None:
        """check_orphaned_dependencies must not change any task."""
        t = Task(id="t1", title="Orphan", depends_on=["ghost"])
        ledger = make_ledger(tmp_path, [t])
        before = snapshot_tasks(ledger)

        gc = EntropyGC(ledger=ledger)
        gc.check_orphaned_dependencies()
        assert snapshot_tasks(ledger) == before


# ── Check 3: circular dependencies ────────────────────────────────────────────


class TestCheckCircularDependencies:
    def test_detects_direct_cycle(self, tmp_path: Path) -> None:
        """Should flag a direct A→B→A cycle."""
        a = Task(id="a", title="A", depends_on=["b"])
        b = Task(id="b", title="B", depends_on=["a"])
        ledger = make_ledger(tmp_path, [a, b])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_circular_dependencies()
        issue_types = [i.issue_type for i in issues]
        assert IssueType.CIRCULAR_DEPENDENCY in issue_types

    def test_detects_transitive_cycle(self, tmp_path: Path) -> None:
        """Should flag a transitive A→B→C→A cycle."""
        a = Task(id="a", title="A", depends_on=["c"])
        b = Task(id="b", title="B", depends_on=["a"])
        c = Task(id="c", title="C", depends_on=["b"])
        ledger = make_ledger(tmp_path, [a, b, c])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_circular_dependencies()
        assert any(i.issue_type == IssueType.CIRCULAR_DEPENDENCY for i in issues)

    def test_no_false_positive_for_linear_chain(self, tmp_path: Path) -> None:
        """A linear A→B→C chain must NOT be flagged as circular."""
        a = Task(id="a", title="A")
        b = Task(id="b", title="B", depends_on=["a"])
        c = Task(id="c", title="C", depends_on=["b"])
        ledger = make_ledger(tmp_path, [a, b, c])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_circular_dependencies()
        assert not any(i.issue_type == IssueType.CIRCULAR_DEPENDENCY for i in issues)

    def test_cycle_check_never_mutates_ledger(self, tmp_path: Path) -> None:
        """check_circular_dependencies must not change any task."""
        a = Task(id="a", title="A", depends_on=["b"])
        b = Task(id="b", title="B", depends_on=["a"])
        ledger = make_ledger(tmp_path, [a, b])
        before = snapshot_tasks(ledger)

        gc = EntropyGC(ledger=ledger)
        gc.check_circular_dependencies()
        assert snapshot_tasks(ledger) == before


# ── Check 4: abandoned with pending dependents ────────────────────────────────


class TestCheckAbandonedWithPendingDependents:
    def test_detects_blocked_chain(self, tmp_path: Path) -> None:
        """Should flag PENDING tasks whose dependency is ABANDONED."""
        # max_retries=0 means one failure immediately abandons the task
        blocker = Task(id="blocker", title="Blocker", max_retries=0)
        dep = Task(id="dep", title="Dependent", depends_on=["blocker"])
        ledger = make_ledger(tmp_path, [blocker, dep])
        # Abandon the blocker in one step
        ledger.claim("blocker", runner_id="r1")
        ledger.mark_failed("blocker", "permanent failure")

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_abandoned_with_pending_dependents()
        assert any(i.issue_type == IssueType.ABANDONED_BLOCKS_PENDING for i in issues)

    def test_abandoned_check_never_mutates_ledger(self, tmp_path: Path) -> None:
        """check_abandoned_with_pending_dependents must not change any task."""
        t = Task(id="t1", title="T1")
        ledger = make_ledger(tmp_path, [t])
        before = snapshot_tasks(ledger)

        gc = EntropyGC(ledger=ledger)
        gc.check_abandoned_with_pending_dependents()
        assert snapshot_tasks(ledger) == before


# ── Check 5: missing required fields ─────────────────────────────────────────


class TestCheckMissingRequiredFields:
    def test_detects_task_with_empty_title(self, tmp_path: Path) -> None:
        """Should flag tasks with an empty title."""
        t = Task(id="t1", title="", description="Has desc")
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_missing_required_fields()
        assert any(
            i.task_id == "t1" and i.issue_type == IssueType.MISSING_REQUIRED_FIELD for i in issues
        )

    def test_detects_task_with_empty_description(self, tmp_path: Path) -> None:
        """Should flag tasks with an empty description."""
        t = Task(id="t1", title="Has title", description="")
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_missing_required_fields()
        assert any(
            i.task_id == "t1" and i.issue_type == IssueType.MISSING_REQUIRED_FIELD for i in issues
        )

    def test_no_flag_for_complete_task(self, tmp_path: Path) -> None:
        """A task with both title and description must not be flagged."""
        t = Task(id="t1", title="Good", description="Good description")
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_missing_required_fields()
        assert not any(i.task_id == "t1" for i in issues)


# ── Check 6: priority outliers ────────────────────────────────────────────────


class TestCheckPriorityOutliers:
    def test_detects_out_of_range_priority(self, tmp_path: Path) -> None:
        """Should flag tasks with priority outside 0–100."""
        t = Task(id="t1", title="Bad priority", priority=999)
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_priority_outliers()
        assert any(i.task_id == "t1" and i.issue_type == IssueType.PRIORITY_OUTLIER for i in issues)

    def test_no_flag_for_valid_priority(self, tmp_path: Path) -> None:
        """A task with priority in 0–100 must not be flagged."""
        t = Task(id="t1", title="Valid", priority=50)
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_priority_outliers()
        assert not any(i.task_id == "t1" for i in issues)


# ── Check 7: retry exhaustion ─────────────────────────────────────────────────


class TestCheckRetryExhaustion:
    def test_detects_task_at_max_retries(self, tmp_path: Path) -> None:
        """Should flag tasks where retry_count >= max_retries."""
        t = Task(id="t1", title="Exhausted", retry_count=3, max_retries=3)
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_retry_exhaustion()
        assert any(i.task_id == "t1" and i.issue_type == IssueType.RETRY_EXHAUSTION for i in issues)

    def test_no_flag_for_task_with_retries_left(self, tmp_path: Path) -> None:
        """A task with retry_count < max_retries must not be flagged."""
        t = Task(id="t1", title="Still going", retry_count=1, max_retries=3)
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_retry_exhaustion()
        assert not any(i.task_id == "t1" for i in issues)


# ── Check 8: duplicate task IDs ───────────────────────────────────────────────


class TestCheckDuplicateTaskIds:
    def test_detects_duplicate_ids_via_mocked_raw_data(self, tmp_path: Path) -> None:
        """Should flag duplicate IDs when raw ledger data has tasks as a list (legacy format)."""

        ledger = make_ledger(tmp_path, [])
        # Mock _read_raw to return tasks as a list (legacy format with duplicates)
        list_format: dict[str, Any] = {
            "schema_version": 1,
            "tasks": [
                {"id": "dup", "title": "First"},
                {"id": "dup", "title": "Second"},
                {"id": "unique", "title": "Unique"},
            ],
        }
        with patch.object(ledger, "_read_raw", return_value=list_format):
            gc = EntropyGC(ledger=ledger)
            issues = gc.check_duplicate_task_ids()

        assert any(
            i.issue_type == IssueType.DUPLICATE_TASK_ID and i.task_id == "dup" for i in issues
        )
        # The unique task should NOT be flagged
        assert not any(i.task_id == "unique" for i in issues)

    def test_no_duplicates_in_dict_format(self, tmp_path: Path) -> None:
        """Modern dict-format ledger has no structural duplicates — should return empty list."""
        ledger = make_ledger(tmp_path, [Task(id="t1", title="T1", description="D")])

        gc = EntropyGC(ledger=ledger)
        issues = gc.check_duplicate_task_ids()
        # Dict format means structurally no duplicates
        assert all(i.issue_type != IssueType.DUPLICATE_TASK_ID for i in issues)


# ── Check 9: progress stall ───────────────────────────────────────────────────


class TestCheckProgressStall:
    def test_detects_failed_tasks_not_retried(self, tmp_path: Path) -> None:
        """Should flag FAILED tasks that haven't been retried within the window."""
        t = Task(id="t1", title="Stalled failed")
        ledger = make_ledger(tmp_path, [t])
        ledger.claim("t1", runner_id="r1")
        ledger.submit_result("t1", TaskResult(raw_output="bad"))
        ledger.mark_failed("t1", "error")
        # Manually backdate updated_at
        task = ledger.get("t1")
        from datetime import UTC, datetime, timedelta

        task.updated_at = datetime.now(tz=UTC) - timedelta(hours=25)
        data = ledger._read_raw()  # type: ignore[attr-defined]
        data["tasks"][task.id]["updated_at"] = task.updated_at.isoformat()
        ledger._write_raw(data)  # type: ignore[attr-defined]

        gc = EntropyGC(ledger=ledger, stall_threshold_seconds=3600)
        issues = gc.check_progress_stall()
        assert any(i.task_id == "t1" and i.issue_type == IssueType.PROGRESS_STALL for i in issues)

    def test_stall_check_never_mutates_ledger(self, tmp_path: Path) -> None:
        """check_progress_stall must not change any task."""
        t = Task(id="t1", title="T1")
        ledger = make_ledger(tmp_path, [t])
        before = snapshot_tasks(ledger)

        gc = EntropyGC(ledger=ledger)
        gc.check_progress_stall()
        assert snapshot_tasks(ledger) == before


# ── Full run() method ─────────────────────────────────────────────────────────


class TestEntropyGCRun:
    def test_run_returns_all_issues(self, tmp_path: Path) -> None:
        """run() should aggregate issues from all 9 checks."""
        t = Task(id="t1", title="", description="", depends_on=["ghost"])
        ledger = make_ledger(tmp_path, [t])

        gc = EntropyGC(ledger=ledger, report_path=tmp_path / "entropy_report.md")
        issues = gc.run()

        assert isinstance(issues, list)
        assert all(isinstance(i, EntropyIssue) for i in issues)

    def test_run_writes_report_file(self, tmp_path: Path) -> None:
        """run() should write entropy_report.md."""
        ledger = make_ledger(tmp_path, [])
        report_path = tmp_path / "entropy_report.md"
        gc = EntropyGC(ledger=ledger, report_path=report_path)
        gc.run()

        assert report_path.exists()
        content = report_path.read_text()
        assert "EntropyGC" in content or "entropy" in content.lower()

    def test_run_report_written_atomically(self, tmp_path: Path) -> None:
        """entropy_report.md must be written atomically (no .tmp left behind)."""
        ledger = make_ledger(tmp_path, [])
        report_path = tmp_path / "entropy_report.md"
        gc = EntropyGC(ledger=ledger, report_path=report_path)
        gc.run()

        assert not list(tmp_path.glob("*.tmp"))

    def test_run_on_clean_ledger_returns_no_issues(self, tmp_path: Path) -> None:
        """A healthy ledger should produce zero entropy issues."""
        tasks = [
            Task(id=f"t{i}", title=f"Task {i}", description=f"Description {i}") for i in range(3)
        ]
        ledger = make_ledger(tmp_path, tasks)
        report_path = tmp_path / "entropy_report.md"
        gc = EntropyGC(ledger=ledger, report_path=report_path)
        issues = gc.run()

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_run_never_mutates_ledger(self, tmp_path: Path) -> None:
        """run() must NEVER change any task status or field."""
        tasks = [Task(id=f"t{i}", title=f"T{i}", description=f"D{i}") for i in range(3)]
        tasks[1].depends_on = [tasks[0].id]
        ledger = make_ledger(tmp_path, tasks)
        before = snapshot_tasks(ledger)

        gc = EntropyGC(ledger=ledger, report_path=tmp_path / "entropy_report.md")
        gc.run()

        assert snapshot_tasks(ledger) == before, "EntropyGC mutated ledger — forbidden!"

    def test_run_report_with_multiple_issues(self, tmp_path: Path) -> None:
        """Report with issues should list each issue type in the markdown."""
        t = Task(id="t1", title="", description="", depends_on=["ghost"])
        ledger = make_ledger(tmp_path, [t])
        report_path = tmp_path / "entropy_report.md"
        gc = EntropyGC(ledger=ledger, report_path=report_path)
        gc.run()

        content = report_path.read_text()
        # Should mention at least one issue type
        assert any(itype.value in content for itype in IssueType)

    def test_write_report_handles_os_error_gracefully(self, tmp_path: Path) -> None:
        """_write_report should log and not raise on OS errors."""

        ledger = make_ledger(tmp_path, [])
        report_path = tmp_path / "entropy_report.md"
        gc = EntropyGC(ledger=ledger, report_path=report_path)

        with patch("os.replace", side_effect=OSError("disk full")):
            # Should not raise — errors are swallowed and logged
            gc._write_report([])  # type: ignore[attr-defined]
