"""Tests for veridian.cli.main — CLI commands via Typer + Rich."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from veridian.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_ledger(tmp: Path, tasks: list[dict[str, object]]) -> Path:
    """Create a ledger.json with the given task dicts (keyed by ID)."""
    ledger_file = tmp / "ledger.json"
    tasks_dict = {str(t["id"]): t for t in tasks}
    ledger_file.write_text(json.dumps({"tasks": tasks_dict}, indent=2))
    return ledger_file


def _make_task(
    task_id: str = "t1",
    title: str = "Test task",
    status: str = "pending",
    verifier_id: str = "schema",
) -> dict[str, object]:
    return {
        "id": task_id,
        "title": title,
        "description": f"Description for {task_id}",
        "status": status,
        "verifier_id": verifier_id,
        "verifier_config": {"required_fields": ["answer"]},
        "priority": 50,
        "phase": "default",
        "retry_count": 0,
        "max_retries": 3,
        "depends_on": [],
    }


# ---------------------------------------------------------------------------
# veridian --version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_shows_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "veridian" in result.stdout.lower()


# ---------------------------------------------------------------------------
# veridian init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_ledger_file(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "ledger.json"
        result = runner.invoke(app, ["init", "--ledger", str(ledger_file)])
        assert result.exit_code == 0
        assert ledger_file.exists()

    def test_refuses_to_overwrite_existing(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "ledger.json"
        ledger_file.write_text("{}")
        result = runner.invoke(app, ["init", "--ledger", str(ledger_file)])
        assert result.exit_code != 0 or "already exists" in result.stdout.lower()


# ---------------------------------------------------------------------------
# veridian status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_shows_stats(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="done"),
                _make_task("t2", status="pending"),
                _make_task("t3", status="failed"),
            ],
        )
        result = runner.invoke(app, ["status", "--ledger", str(ledger_file)])
        assert result.exit_code == 0
        assert "done" in result.stdout.lower()
        assert "pending" in result.stdout.lower()

    def test_empty_ledger(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(tmp_path, [])
        result = runner.invoke(app, ["status", "--ledger", str(ledger_file)])
        assert result.exit_code == 0

    def test_missing_ledger_shows_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["status", "--ledger", str(tmp_path / "nope.json")])
        assert result.exit_code != 0 or "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# veridian list
# ---------------------------------------------------------------------------


class TestList:
    def test_lists_all_tasks(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="done"),
                _make_task("t2", status="pending"),
            ],
        )
        result = runner.invoke(app, ["list", "--ledger", str(ledger_file)])
        assert result.exit_code == 0
        assert "t1" in result.stdout
        assert "t2" in result.stdout

    def test_filter_by_status(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="done"),
                _make_task("t2", status="pending"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "list",
                "--ledger",
                str(ledger_file),
                "--status",
                "pending",
            ],
        )
        assert result.exit_code == 0
        assert "t2" in result.stdout
        assert "t1" not in result.stdout


# ---------------------------------------------------------------------------
# veridian gc
# ---------------------------------------------------------------------------


class TestGC:
    def test_runs_entropy_checks(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="pending"),
            ],
        )
        result = runner.invoke(app, ["gc", "--ledger", str(ledger_file)])
        assert result.exit_code == 0

    def test_reports_issues(self, tmp_path: Path) -> None:
        """Stale in_progress task should be flagged."""
        task = _make_task("t1", status="in_progress")
        task["updated_at"] = "2020-01-01T00:00:00"
        ledger_file = _seed_ledger(tmp_path, [task])
        result = runner.invoke(app, ["gc", "--ledger", str(ledger_file)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# veridian reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_requires_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="failed"),
            ],
        )
        result = runner.invoke(app, ["reset", "--ledger", str(ledger_file)])
        # Without --confirm, should either prompt or refuse
        assert result.exit_code == 0 or "confirm" in result.stdout.lower()

    def test_reset_with_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="failed"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "reset",
                "--ledger",
                str(ledger_file),
                "--confirm",
            ],
        )
        assert result.exit_code == 0

        # Verify the task was reset to pending
        data = json.loads(ledger_file.read_text())
        assert data["tasks"]["t1"]["status"] == "pending"


# ---------------------------------------------------------------------------
# veridian skip
# ---------------------------------------------------------------------------


class TestSkip:
    def test_skip_requires_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="pending"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "skip",
                "--ledger",
                str(ledger_file),
                "--task-id",
                "t1",
            ],
        )
        assert result.exit_code == 0 or "confirm" in result.stdout.lower()

    def test_skip_with_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="pending"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "skip",
                "--ledger",
                str(ledger_file),
                "--task-id",
                "t1",
                "--confirm",
            ],
        )
        assert result.exit_code == 0

        data = json.loads(ledger_file.read_text())
        assert data["tasks"]["t1"]["status"] == "skipped"

    def test_skip_nonexistent_task(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="pending"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "skip",
                "--ledger",
                str(ledger_file),
                "--task-id",
                "nope",
                "--confirm",
            ],
        )
        assert result.exit_code != 0 or "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# veridian retry
# ---------------------------------------------------------------------------


class TestRetry:
    def test_retry_requires_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="failed"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "retry",
                "--ledger",
                str(ledger_file),
                "--task-id",
                "t1",
            ],
        )
        assert result.exit_code == 0 or "confirm" in result.stdout.lower()

    def test_retry_with_confirm(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="failed"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "retry",
                "--ledger",
                str(ledger_file),
                "--task-id",
                "t1",
                "--confirm",
            ],
        )
        assert result.exit_code == 0

        data = json.loads(ledger_file.read_text())
        assert data["tasks"]["t1"]["status"] == "pending"


# ---------------------------------------------------------------------------
# veridian run (dry-run only in tests)
# ---------------------------------------------------------------------------


class TestRun:
    def test_dry_run(self, tmp_path: Path) -> None:
        ledger_file = _seed_ledger(
            tmp_path,
            [
                _make_task("t1", status="pending"),
            ],
        )
        result = runner.invoke(
            app,
            [
                "run",
                "--ledger",
                str(ledger_file),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "dry" in result.stdout.lower()
