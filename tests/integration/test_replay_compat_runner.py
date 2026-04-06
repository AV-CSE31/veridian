"""
tests.integration.test_replay_compat_runner
────────────────────────────────────────────
RV3-003 end-to-end: runner persists replay snapshot on first run and fails
closed on mismatch in strict mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

_SCHEMA_CONFIG = {"required_fields": ["summary"]}


def _passing_provider(model: str = "mock/v1") -> MockProvider:
    provider = MockProvider()
    # MockProvider.script_veridian_result sets LLMResponse.model to "mock";
    # override via callable so we can vary the model between runs.
    from veridian.providers.base import LLMResponse

    def _responder(messages: Any, **kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=(
                "<veridian:result>\n"
                '{"summary": "done", "structured": {"summary": "done"}, "artifacts": []}\n'
                "</veridian:result>"
            ),
            input_tokens=200,
            output_tokens=50,
            model=model,
        )

    provider.respond_with(_responder)
    # Expose .model attribute on the provider for the replay snapshot builder.
    provider.model = model  # type: ignore[attr-defined]
    return provider


def _make_task(title: str = "t1", **kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "title": title,
        "verifier_id": "schema",
        "verifier_config": _SCHEMA_CONFIG,
    }
    defaults.update(kwargs)
    return Task(**defaults)


@pytest.fixture
def strict_config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        strict_replay=True,
    )


@pytest.fixture
def loose_config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        strict_replay=False,
    )


class TestSnapshotPersistence:
    def test_first_run_persists_snapshot_on_success(self, strict_config: VeridianConfig) -> None:
        ledger = TaskLedger(
            path=strict_config.ledger_file, progress_file=str(strict_config.progress_file)
        )
        task = _make_task()
        ledger.add([task])

        runner = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=strict_config,
            hooks=HookRegistry(),
        )
        summary = runner.run()

        assert summary.done_count == 1
        final = ledger.get(task.id)
        assert final.status == TaskStatus.DONE
        assert "run_replay_snapshot" in final.result.extras
        snap = final.result.extras["run_replay_snapshot"]
        assert snap["model_id"] == "mock/v1"
        assert snap["verifier_id"] == "schema"
        assert snap["prompt_hash"]
        assert snap["verifier_config_hash"]


class TestStrictReplayFailsClosed:
    def test_model_change_between_runs_fails_closed_on_retry(
        self, strict_config: VeridianConfig
    ) -> None:
        """A failed task that retries under a different provider must fail
        closed with a replay_incompatible error rather than silently diverge."""
        ledger = TaskLedger(
            path=strict_config.ledger_file, progress_file=str(strict_config.progress_file)
        )
        # First task verifier passes; we succeed and persist the snapshot.
        task = _make_task(max_retries=5)
        ledger.add([task])

        VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(model="mock/v1"),
            config=strict_config,
            hooks=HookRegistry(),
        ).run()
        assert ledger.get(task.id).status == TaskStatus.DONE
        baseline_snap = ledger.get(task.id).result.extras["run_replay_snapshot"]
        assert baseline_snap["model_id"] == "mock/v1"

        # Simulate re-run by resetting the task to PENDING (operator retry)
        # while preserving the persisted result (and thus the baseline snapshot).
        # reset_failed() won't help because the task is DONE; manipulate via
        # checkpoint_result + ledger internals through a fresh Task copy.
        done_task = ledger.get(task.id)
        done_task.status = TaskStatus.PENDING
        done_task.claimed_by = None
        ledger.add([done_task], skip_duplicates=False)

        # Second runner uses a DIFFERENT model. Strict replay must fail closed.
        summary = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(model="mock/v2"),
            config=strict_config,
            hooks=HookRegistry(),
        ).run()

        final = ledger.get(task.id)
        assert final.status in {TaskStatus.FAILED, TaskStatus.ABANDONED, TaskStatus.PENDING}, (
            f"Expected strict replay to fail closed, got {final.status}"
        )
        assert final.last_error is not None
        assert "replay_incompatible" in final.last_error
        assert "model_id" in final.last_error
        assert summary.done_count == 0


class TestLooseReplayAllowsDivergence:
    def test_model_change_under_loose_replay_still_completes(
        self, loose_config: VeridianConfig
    ) -> None:
        ledger = TaskLedger(
            path=loose_config.ledger_file, progress_file=str(loose_config.progress_file)
        )
        task = _make_task(max_retries=5)
        ledger.add([task])

        VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(model="mock/v1"),
            config=loose_config,
            hooks=HookRegistry(),
        ).run()
        assert ledger.get(task.id).status == TaskStatus.DONE

        # Reset to PENDING then rerun with a new model — loose mode permits it.
        done_task = ledger.get(task.id)
        done_task.status = TaskStatus.PENDING
        done_task.claimed_by = None
        ledger.add([done_task], skip_duplicates=False)

        summary = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(model="mock/v2"),
            config=loose_config,
            hooks=HookRegistry(),
        ).run()
        assert summary.done_count == 1
        assert ledger.get(task.id).status == TaskStatus.DONE
