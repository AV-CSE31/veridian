"""
veridian.loop.parallel_runner
──────────────────────────────
ParallelRunner — async task execution with bounded concurrency via asyncio.Semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from veridian.core.config import VeridianConfig
from veridian.core.events import RunCompleted, RunStarted, TaskResumed
from veridian.core.exceptions import ControlFlowSignal
from veridian.core.task import TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.loop.runtime_store import RuntimeStore
from veridian.loop.scheduler import AsyncScheduler
from veridian.providers.base import LLMProvider
from veridian.verify.base import VerifierRegistry

__all__ = ["ParallelRunner"]

log = logging.getLogger(__name__)


class ParallelRunner:
    """
    Async task execution loop with bounded concurrency.
    Uses asyncio.Semaphore(max_parallel) to cap concurrent task slots.

    Usage::

        runner = ParallelRunner(ledger=ledger, provider=provider, config=config)
        summary = await runner.run_async()
    """

    def __init__(
        self,
        ledger: RuntimeStore,
        provider: LLMProvider,
        config: VeridianConfig | None = None,
        hooks: HookRegistry | None = None,
        verifier_registry: VerifierRegistry | None = None,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.config = config or VeridianConfig()
        self.hooks = hooks or HookRegistry()
        self._verifier_registry = verifier_registry

    async def run_async(self, phase: str | None = None) -> RunSummary:
        """
        Dispatch all pending tasks concurrently up to max_parallel.
        Returns RunSummary when all tasks are done or the queue is empty.
        """
        start_time = time.monotonic()
        run_id = str(uuid.uuid4())[:8]
        phase = phase or self.config.phase
        summary = RunSummary(run_id=run_id, dry_run=self.config.dry_run, phase=phase)

        # Crash recovery — always first
        self.ledger.reset_in_progress()

        # RV3-010 parity: total task count follows sync semantics (pending +
        # optionally paused), but execution scheduling itself is dependency-aware.
        tasks_to_run = self.ledger.list(status=TaskStatus.PENDING)
        if bool(getattr(self.config, "resume_paused_on_start", True)):
            tasks_to_run = list(self.ledger.list(status=TaskStatus.PAUSED)) + tasks_to_run
        if phase:
            tasks_to_run = [t for t in tasks_to_run if t.phase == phase]

        summary.total_tasks = len(tasks_to_run)
        if not tasks_to_run:
            summary.duration_seconds = time.monotonic() - start_time
            return summary

        self.hooks.fire(
            "before_run",
            RunStarted(run_id=run_id, total_tasks=summary.total_tasks, phase=phase),
        )

        async def _dispatch(task_id: str) -> RunSummary:
            # `_run_single_task` is sync today; `to_thread` prevents event-loop
            # blocking while AsyncScheduler enforces bounded concurrency.
            return await asyncio.to_thread(self._run_single_task, task_id, run_id)

        # Keep track of tasks that paused during THIS run so we do not
        # immediately re-resume them and spin in a pause→resume loop.
        paused_this_run: set[str] = set()
        # Guard against infinite loops: every task id that has been dispatched
        # at least once in this run is excluded from subsequent scheduler
        # passes. This also tolerates tests that monkey-patch _run_single_task
        # to return a RunSummary without mutating ledger state.
        dispatched_this_run: set[str] = set()
        while True:
            task_ids = self._list_schedulable_task_ids(
                phase=phase,
                paused_this_run=paused_this_run,
                dispatched_this_run=dispatched_this_run,
            )
            if not task_ids:
                break

            batch_scheduler = AsyncScheduler(max_concurrency=self.config.max_parallel)

            def _make_task_fn(task_id: str) -> Callable[[], Awaitable[RunSummary]]:
                async def _run_one() -> RunSummary:
                    return await _dispatch(task_id)

                return _run_one

            task_fns = [_make_task_fn(task_id) for task_id in task_ids]
            try:
                per_task_summaries = await batch_scheduler.run(task_fns)
            except ExceptionGroup as exc_group:
                log.warning(
                    "parallel_runner.batch_errors count=%d",
                    len(exc_group.exceptions),
                )
                per_task_summaries = []
                for exc in exc_group.exceptions:
                    err_summary = RunSummary(
                        run_id=run_id,
                        dry_run=self.config.dry_run,
                        phase=phase,
                    )
                    err_summary.failed_count = 1
                    err_summary.errors.append(str(exc))
                    per_task_summaries.append(err_summary)
            dispatched_this_run.update(task_ids)
            for item in per_task_summaries:
                summary.done_count += item.done_count
                summary.failed_count += item.failed_count
                summary.abandoned_count += item.abandoned_count
                if item.errors:
                    summary.errors.extend(item.errors)

            for task_id in task_ids:
                try:
                    task = self.ledger.get(task_id)
                except Exception:
                    continue
                if task.status == TaskStatus.PAUSED:
                    paused_this_run.add(task_id)

        summary.duration_seconds = time.monotonic() - start_time
        self.hooks.fire("after_run", RunCompleted(run_id=run_id, summary=summary))
        return summary

    def _list_schedulable_task_ids(
        self,
        *,
        phase: str | None,
        paused_this_run: set[str],
        dispatched_this_run: set[str],
    ) -> list[str]:
        """Return dependency-aware schedulable task IDs in deterministic order.

        Order and filters mirror the sync runner's scheduler semantics:
        1. PAUSED tasks first (if resume_paused_on_start), excluding tasks
           already paused or dispatched during this run.
        2. PENDING tasks whose dependencies are DONE, excluding tasks already
           dispatched in this run (prevents infinite loops when a dispatch
           does not mutate ledger state, e.g. in monkey-patched tests).
        3. Sort by priority DESC then created_at ASC.
        """
        tasks = self.ledger.list()
        done_ids = {task.id for task in tasks if task.status == TaskStatus.DONE}

        schedulable: list[str] = []
        include_paused = bool(getattr(self.config, "resume_paused_on_start", True))
        if include_paused:
            paused = [
                task
                for task in tasks
                if task.status == TaskStatus.PAUSED
                and task.id not in paused_this_run
                and task.id not in dispatched_this_run
                and (phase is None or task.phase == phase)
            ]
            paused.sort(key=lambda t: (-t.priority, t.created_at))
            schedulable.extend(task.id for task in paused)

        pending = [
            task
            for task in tasks
            if task.status == TaskStatus.PENDING
            and task.id not in dispatched_this_run
            and (phase is None or task.phase == phase)
            and all(dep in done_ids for dep in task.depends_on)
        ]
        pending.sort(key=lambda t: (-t.priority, t.created_at))
        schedulable.extend(task.id for task in pending)
        return schedulable

    def _run_single_task(self, task_id: str, run_id: str) -> RunSummary:
        """Run a single task synchronously (called from executor).

        RV3-010: mirrors the sync runner's control-flow handling so PAUSED
        tasks are resumed, ControlFlowSignal propagates to ledger.pause(), and
        summary counts stay consistent with sync mode.
        """
        local_summary = RunSummary(
            run_id=run_id, dry_run=self.config.dry_run, phase=self.config.phase
        )
        runner = VeridianRunner(
            ledger=self.ledger,
            provider=self.provider,
            config=self.config,
            hooks=self.hooks,
            verifier_registry=self._verifier_registry,
        )
        task = self.ledger.get(task_id)

        # RV3-010: resume PAUSED tasks before dispatch, matching sync loop.
        if task.status == TaskStatus.PAUSED:
            try:
                task = self.ledger.resume(task.id, run_id)
            except Exception as exc:
                log.warning("parallel_runner.resume_failed task_id=%s err=%s", task_id, exc)
                local_summary.errors.append(f"resume_failed: {exc}")
                return local_summary
            resume_count = 0
            if task.result is not None:
                resume_count = int(
                    task.result.extras.get("pause_payload", {}).get("resume_count", 0)
                )
            self.hooks.fire(
                "on_resume",
                TaskResumed(run_id=run_id, task=task, resume_count=resume_count),
            )

        try:
            runner._process_task(task, run_id, local_summary)
        except ControlFlowSignal as signal:
            # RV3-002/010: pause signals must not count as task errors.
            runner._handle_pause_signal(task, run_id, signal, local_summary)
        except Exception as exc:
            log.error("parallel_runner.task_error task_id=%s err=%s", task_id, exc)
            local_summary.failed_count += 1
            local_summary.errors.append(str(exc))
        return local_summary
