"""
tests.integration.test_replay_cli
──────────────────────────────────
RV3-006: smoke tests for `veridian replay` CLI using the Typer test runner.
Verifies that an operator can answer "what changed?" via CLI-only workflow.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from veridian.cli.main import app
from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

_SCHEMA_CONFIG = {"required_fields": ["summary"]}


def _seed_done_task(tmp_path: Path, *, model: str = "mock/v1") -> tuple[Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_path / "ledger.json"
    progress_path = tmp_path / "progress.md"
    ledger = TaskLedger(path=ledger_path, progress_file=str(progress_path))
    task = Task(title="cli_test", verifier_id="schema", verifier_config=_SCHEMA_CONFIG)
    ledger.add([task])

    provider = MockProvider()
    provider.script_veridian_result({"summary": "done"})
    provider.script_veridian_result({"summary": "done"})
    provider.model = model  # type: ignore[attr-defined]

    config = VeridianConfig(
        max_turns_per_task=3,
        ledger_file=ledger_path,
        progress_file=progress_path,
        strict_replay=False,
        activity_journal_enabled=True,
    )
    VeridianRunner(ledger=ledger, provider=provider, config=config, hooks=HookRegistry()).run()
    assert ledger.get(task.id).status == TaskStatus.DONE
    return ledger_path, task.id


class TestReplayShow:
    def test_show_emits_task_metadata_and_journal(self, tmp_path: Path) -> None:
        ledger_path, task_id = _seed_done_task(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            app, ["replay", "show", task_id, "--ledger", str(ledger_path), "--json"]
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["task_id"] == task_id
        assert payload["status"] == "done"
        assert "run_replay_snapshot" in payload
        assert isinstance(payload["activity_journal"], list)
        assert len(payload["activity_journal"]) >= 1

    def test_show_missing_task_exits_nonzero(self, tmp_path: Path) -> None:
        ledger_path, _ = _seed_done_task(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["replay", "show", "nonexistent", "--ledger", str(ledger_path)])
        assert result.exit_code != 0


class TestReplayCompare:
    def test_compare_detects_snapshot_drift(self, tmp_path: Path) -> None:
        ledger_a, task_id = _seed_done_task(tmp_path / "a", model="mock/v1")

        # Second ledger: same task id, different model
        ledger_b_dir = tmp_path / "b"
        ledger_b_dir.mkdir()
        ledger_b_path = ledger_b_dir / "ledger.json"
        progress_b = ledger_b_dir / "progress.md"
        ledger_b_obj = TaskLedger(path=ledger_b_path, progress_file=str(progress_b))
        task = Task(
            id=task_id,
            title="cli_test",
            verifier_id="schema",
            verifier_config=_SCHEMA_CONFIG,
        )
        ledger_b_obj.add([task])
        provider_b = MockProvider()
        provider_b.script_veridian_result({"summary": "done"})
        provider_b.model = "mock/v2"  # type: ignore[attr-defined]
        config_b = VeridianConfig(
            max_turns_per_task=3,
            ledger_file=ledger_b_path,
            progress_file=progress_b,
            activity_journal_enabled=True,
        )
        VeridianRunner(
            ledger=ledger_b_obj,
            provider=provider_b,
            config=config_b,
            hooks=HookRegistry(),
        ).run()

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "replay",
                "compare",
                task_id,
                str(ledger_a),
                str(ledger_b_path),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        snap_diffs = payload.get("snapshot_diffs", [])
        assert any(d["field"] == "model_id" for d in snap_diffs), (
            f"Expected model_id drift in snapshot_diffs, got {snap_diffs}"
        )
