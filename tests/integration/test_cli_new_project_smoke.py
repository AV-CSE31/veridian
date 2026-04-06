"""End-to-end CLI smoke gate for a brand-new project.

WCP-004 acceptance: ``init -> run -> status -> replay`` should work on a
freshly bootstrapped ledger without manual JSON edits.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from veridian.cli.main import app
from veridian.core.task import Task
from veridian.ledger.ledger import TaskLedger

runner = CliRunner()


def test_new_project_smoke_flow(tmp_path: Path) -> None:
    ledger_file = tmp_path / "ledger.json"
    progress_file = tmp_path / "progress.md"

    init_result = runner.invoke(app, ["init", "--ledger", str(ledger_file)])
    assert init_result.exit_code == 0
    assert ledger_file.exists()

    # Seed one deterministic task so "run --dry-run" and "replay show" have
    # concrete data to operate on.
    ledger = TaskLedger(path=ledger_file, progress_file=str(progress_file))
    task = Task(
        title="Smoke task",
        description="Verify CLI smoke path",
        verifier_id="schema",
        verifier_config={"required_fields": ["answer"]},
    )
    ledger.add([task])

    run_result = runner.invoke(app, ["run", "--ledger", str(ledger_file), "--dry-run"])
    assert run_result.exit_code == 0

    status_result = runner.invoke(app, ["status", "--ledger", str(ledger_file)])
    assert status_result.exit_code == 0
    assert "total tasks" in status_result.stdout.lower()

    replay_result = runner.invoke(
        app,
        ["replay", "show", task.id, "--ledger", str(ledger_file), "--json"],
    )
    assert replay_result.exit_code == 0
    assert task.id in replay_result.stdout
