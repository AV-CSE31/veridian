"""
veridian.loop.parallel_runner
──────────────────────────────
ParallelRunner — async task execution with bounded concurrency via asyncio.Semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from veridian.core.config import VeridianConfig
from veridian.core.task import TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary, VeridianRunner
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
        ledger: TaskLedger,
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
        run_id = str(uuid.uuid4())[:8]
        phase = phase or self.config.phase
        semaphore = asyncio.Semaphore(self.config.max_parallel)
        summary = RunSummary(run_id=run_id, dry_run=self.config.dry_run, phase=phase)

        # Crash recovery — always first
        self.ledger.reset_in_progress()

        tasks_to_run = self.ledger.list(status=TaskStatus.PENDING)
        if phase:
            tasks_to_run = [t for t in tasks_to_run if t.phase == phase]

        summary.total_tasks = len(tasks_to_run)
        if not tasks_to_run:
            return summary

        async def _dispatch(task_id: str) -> None:
            async with semaphore:
                loop = asyncio.get_event_loop()
                # Run the synchronous VeridianRunner for a single task in executor
                await loop.run_in_executor(
                    None,
                    self._run_single_task,
                    task_id,
                    run_id,
                    summary,
                )

        await asyncio.gather(*[_dispatch(t.id) for t in tasks_to_run])
        return summary

    def _run_single_task(self, task_id: str, run_id: str, summary: RunSummary) -> None:
        """Run a single task synchronously (called from executor)."""
        runner = VeridianRunner(
            ledger=self.ledger,
            provider=self.provider,
            config=self.config,
            hooks=self.hooks,
            verifier_registry=self._verifier_registry,
        )
        task = self.ledger.get(task_id)
        try:
            runner._process_task(task, run_id, summary)
        except Exception as exc:
            log.error("parallel_runner.task_error task_id=%s err=%s", task_id, exc)
            summary.failed_count += 1
            summary.errors.append(str(exc))
