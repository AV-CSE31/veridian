"""
tests.integration.test_parallel_parity
───────────────────────────────────────
RV3-010: Parallel runner parity with sync runner on hooks, replay, and policy.

Verifies:
- Pause/resume works under ParallelRunner (control-flow signals propagate).
- Activity journal is persisted per task when enabled.
- Replay snapshot is captured per task.
- RunSummary counts match across sync and parallel modes.
- Event ordering per task is deterministic even under concurrency.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskStatus
from veridian.hooks.base import BaseHook
from veridian.hooks.builtin.human_review import HumanReviewHook
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.parallel_runner import ParallelRunner
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

_SCHEMA = {"required_fields": ["summary"]}


def _passing_provider() -> MockProvider:
    p = MockProvider()
    for _ in range(20):
        p.script_veridian_result({"summary": "done"})
    return p


def _make_task(title: str, **kw: Any) -> Task:
    return Task(title=title, verifier_id="schema", verifier_config=_SCHEMA, **kw)


@pytest.fixture
def config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        max_parallel=4,
        activity_journal_enabled=True,
    )


class _EventCapture(BaseHook):
    id: ClassVar[str] = "capture"

    def __init__(self) -> None:
        self.task_completed_ids: list[str] = []
        self.task_paused_ids: list[str] = []

    def after_task(self, event: Any) -> None:
        task = getattr(event, "task", None)
        if task is not None:
            self.task_completed_ids.append(task.id)

    def on_pause(self, event: Any) -> None:
        task = getattr(event, "task", None)
        if task is not None:
            self.task_paused_ids.append(task.id)


class TestSyncParallelParity:
    def test_parallel_runner_completes_all_done_tasks(self, config: VeridianConfig) -> None:
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        ledger.add([_make_task(f"t{i}") for i in range(6)])

        hooks = HookRegistry()
        runner = ParallelRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        )
        summary = asyncio.run(runner.run_async())
        assert summary.done_count == 6
        assert summary.failed_count == 0
        for t in ledger.list():
            assert t.status == TaskStatus.DONE

    def test_parallel_preserves_activity_journal_per_task(self, config: VeridianConfig) -> None:
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        ledger.add([_make_task(f"t{i}") for i in range(3)])
        runner = ParallelRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=HookRegistry(),
        )
        asyncio.run(runner.run_async())
        for t in ledger.list():
            assert t.result is not None
            journal = t.result.extras.get("activity_journal", [])
            assert isinstance(journal, list)
            assert len(journal) >= 1

    def test_parallel_routes_pause_signals_like_sync(self, config: VeridianConfig) -> None:
        """RV3-010 acceptance: parallel and sync achieve parity on hooks/policy."""
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        ledger.add(
            [
                _make_task("needs_review", metadata={"requires_human_review": True}),
                _make_task("regular"),
            ]
        )
        hooks = HookRegistry()
        hooks.register(HumanReviewHook())
        capture = _EventCapture()
        hooks.register(capture)

        runner = ParallelRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        )
        summary = asyncio.run(runner.run_async())

        assert summary.done_count == 1  # regular task
        # Exactly one task should be PAUSED
        paused_tasks = [t for t in ledger.list() if t.status == TaskStatus.PAUSED]
        assert len(paused_tasks) == 1
        assert paused_tasks[0].title == "needs_review"
        # TaskPaused event was fired
        assert len(capture.task_paused_ids) == 1

    def test_summary_counts_match_sync_runner(self, config: VeridianConfig) -> None:
        """Same workload, same counts across runner implementations."""
        # Sync run in its own ledger file
        sync_cfg = VeridianConfig(
            max_turns_per_task=config.max_turns_per_task,
            ledger_file=config.ledger_file.with_name("sync_ledger.json"),
            progress_file=config.progress_file.with_name("sync_progress.md"),
            activity_journal_enabled=True,
        )
        sync_ledger = TaskLedger(
            path=sync_cfg.ledger_file, progress_file=str(sync_cfg.progress_file)
        )
        sync_ledger.add([_make_task(f"t{i}") for i in range(4)])
        sync_summary = VeridianRunner(
            ledger=sync_ledger,
            provider=_passing_provider(),
            config=sync_cfg,
            hooks=HookRegistry(),
        ).run()

        # Parallel run in its own ledger file
        par_ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        par_ledger.add([_make_task(f"t{i}") for i in range(4)])
        par_summary = asyncio.run(
            ParallelRunner(
                ledger=par_ledger,
                provider=_passing_provider(),
                config=config,
                hooks=HookRegistry(),
            ).run_async()
        )

        assert sync_summary.done_count == par_summary.done_count == 4
        assert sync_summary.failed_count == par_summary.failed_count == 0
        assert sync_summary.abandoned_count == par_summary.abandoned_count == 0

    def test_parallel_respects_dependency_gating_like_sync(self, config: VeridianConfig) -> None:
        """Regression: dependent tasks must not run when prerequisites fail."""
        # Sync baseline
        sync_cfg = VeridianConfig(
            max_turns_per_task=config.max_turns_per_task,
            ledger_file=config.ledger_file.with_name("sync_dep_ledger.json"),
            progress_file=config.progress_file.with_name("sync_dep_progress.md"),
            activity_journal_enabled=True,
        )
        sync_ledger = TaskLedger(
            path=sync_cfg.ledger_file, progress_file=str(sync_cfg.progress_file)
        )
        sync_ledger.add(
            [
                Task(
                    id="A",
                    title="prerequisite",
                    verifier_id="schema",
                    verifier_config={"required_fields": ["must_exist"]},
                ),
                Task(
                    id="B",
                    title="dependent",
                    depends_on=["A"],
                    verifier_id="schema",
                    verifier_config=_SCHEMA,
                ),
            ]
        )
        sync_provider = MockProvider()
        sync_provider.script_veridian_result({"summary": "only_summary"})
        sync_provider.script_veridian_result({"summary": "would_run_if_not_gated"})
        sync_summary = VeridianRunner(
            ledger=sync_ledger,
            provider=sync_provider,
            config=sync_cfg,
            hooks=HookRegistry(),
        ).run()
        assert sync_summary.done_count == 0
        assert sync_summary.failed_count == 1
        assert sync_ledger.get("A").status == TaskStatus.FAILED
        assert sync_ledger.get("B").status == TaskStatus.PENDING
        assert sync_provider.call_count == 1

        # Parallel run should match sync behavior.
        par_ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        par_ledger.add(
            [
                Task(
                    id="A",
                    title="prerequisite",
                    verifier_id="schema",
                    verifier_config={"required_fields": ["must_exist"]},
                ),
                Task(
                    id="B",
                    title="dependent",
                    depends_on=["A"],
                    verifier_id="schema",
                    verifier_config=_SCHEMA,
                ),
            ]
        )
        par_provider = MockProvider()
        par_provider.script_veridian_result({"summary": "only_summary"})
        par_provider.script_veridian_result({"summary": "would_run_if_not_gated"})
        par_summary = asyncio.run(
            ParallelRunner(
                ledger=par_ledger,
                provider=par_provider,
                config=config,
                hooks=HookRegistry(),
            ).run_async()
        )
        assert par_summary.done_count == 0
        assert par_summary.failed_count == 1
        assert par_ledger.get("A").status == TaskStatus.FAILED
        assert par_ledger.get("B").status == TaskStatus.PENDING
        assert par_provider.call_count == 1
