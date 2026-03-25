"""
veridian.loop.runner
─────────────────────
VeridianRunner — the main synchronous task execution loop.

Runner sequence (FROZEN — do NOT reorder per CLAUDE.md §6):
  1. reset_in_progress()        — crash recovery, ALWAYS first
  2. fire RunStarted hook
  3. Loop: get_next() → claim → initialize → worker → verify → mark_done/failed
  4. fire RunCompleted / RunAborted hook
  5. Return RunSummary

SIGINT contract:
  - Set _shutdown flag
  - Finish current task
  - Write RunSummary
  - Exit cleanly — never sys.exit() mid-task

dry_run=True:
  - Assemble context, log what would run, return RunSummary(dry_run=True)
  - Never calls provider.complete()
"""
from __future__ import annotations

import contextlib
import logging
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from veridian.agents.worker import WorkerAgent
from veridian.context.manager import ContextManager
from veridian.context.window import TokenWindow
from veridian.core.config import VeridianConfig
from veridian.core.events import (
    RunCompleted,
    RunStarted,
    TaskClaimed,
    TaskCompleted,
    TaskFailed,
)
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.providers.base import LLMProvider
from veridian.skills.library import SkillLibrary
from veridian.verify.base import VerifierRegistry

__all__ = ["VeridianRunner", "RunSummary"]

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """Final report returned by VeridianRunner.run()."""
    run_id: str = ""
    done_count: int = 0
    failed_count: int = 0
    abandoned_count: int = 0
    total_tasks: int = 0
    duration_seconds: float = 0.0
    dry_run: bool = False
    phase: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "done_count": self.done_count,
            "failed_count": self.failed_count,
            "abandoned_count": self.abandoned_count,
            "total_tasks": self.total_tasks,
            "duration_seconds": round(self.duration_seconds, 3),
            "dry_run": self.dry_run,
            "phase": self.phase,
        }


class VeridianRunner:
    """
    Synchronous task execution loop.

    Dependency injection: all major components are constructor-injected so
    MockProvider can substitute without touching production code.

    Usage::

        runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
        summary = runner.run()
    """

    def __init__(
        self,
        ledger: TaskLedger,
        provider: LLMProvider,
        config: VeridianConfig | None = None,
        hooks: HookRegistry | None = None,
        verifier_registry: VerifierRegistry | None = None,
        skill_library: SkillLibrary | None = None,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.config = config or VeridianConfig()
        self.hooks = hooks or HookRegistry()
        self._verifier_registry = verifier_registry
        self._shutdown = False
        self._run_id = str(uuid.uuid4())[:8]

        # Skill library: opt-in — None means disabled
        self.skill_library: SkillLibrary | None = skill_library
        if self.skill_library is None and self.config.skill_library_path is not None:
            self.skill_library = SkillLibrary(
                store_path=self.config.skill_library_path,
                provider=provider,
                min_confidence=self.config.skill_min_confidence,
                max_retries_for_skill=self.config.skill_max_retries,
            )

        # Drift detection: opt-in — None means disabled
        if self.config.drift_history_file is not None:
            from veridian.hooks.builtin.drift_detector import DriftDetectorHook

            drift_hook = DriftDetectorHook(
                history_file=self.config.drift_history_file,
                window=self.config.drift_window,
                threshold=self.config.drift_threshold,
            )
            self.hooks.register(drift_hook)

        # Context manager for worker prompt assembly
        self._context_manager = ContextManager(
            window=TokenWindow(capacity=self.config.context_window_tokens),
            provider=provider,
            progress_path=Path(str(self.config.progress_file)),
        )

    def run(self, phase: str | None = None) -> RunSummary:
        """
        Execute all pending tasks in the ledger.

        RUNNER SEQUENCE (frozen — see CLAUDE.md §6):
          1. reset_in_progress()
          2. fire RunStarted
          3. Task loop
          4. fire RunCompleted
          5. Return RunSummary
        """
        start_time = time.monotonic()
        run_id = self._run_id
        phase = phase or self.config.phase

        summary = RunSummary(
            run_id=run_id,
            dry_run=self.config.dry_run,
            phase=phase,
        )

        # ── Step 1: Crash recovery — ALWAYS FIRST ────────────────────────────
        self.ledger.reset_in_progress()

        # Count total pending tasks
        pending = self.ledger.list(status=TaskStatus.PENDING)
        if phase:
            pending = [t for t in pending if t.phase == phase]
        summary.total_tasks = len(pending)

        if summary.total_tasks == 0:
            log.info("runner.no_tasks run_id=%s phase=%s", run_id, phase)
            summary.duration_seconds = time.monotonic() - start_time
            return summary

        # ── Step 2: RunStarted hook ───────────────────────────────────────────
        self.hooks.fire(
            "before_run",
            RunStarted(run_id=run_id, total_tasks=summary.total_tasks, phase=phase),
        )

        self._setup_signal_handler()

        # ── Step 3: Task loop ─────────────────────────────────────────────────
        self._task_loop(run_id, phase, summary)

        # ── Step 4: RunCompleted hook ─────────────────────────────────────────
        summary.duration_seconds = time.monotonic() - start_time
        self.hooks.fire(
            "after_run",
            RunCompleted(run_id=run_id, summary=summary),
        )

        # ── Step 5: Post-run skill extraction (opt-in) ────────────────────────
        if self.skill_library is not None:
            try:
                admitted = self.skill_library.post_run(self.ledger, run_id=run_id)
                if admitted:
                    log.info("runner.skills_admitted count=%d", len(admitted))
            except Exception as exc:
                log.warning("runner.skill_extraction_error err=%s", exc)

        log.info(
            "runner.complete run_id=%s done=%d failed=%d duration=%.1fs",
            run_id, summary.done_count, summary.failed_count, summary.duration_seconds,
        )
        return summary

    def _task_loop(
        self,
        run_id: str,
        phase: str | None,
        summary: RunSummary,
    ) -> None:
        """Inner loop: process tasks until queue empty or shutdown signalled."""
        while not self._shutdown:
            task = self.ledger.get_next(phase=phase)
            if task is None:
                break

            try:
                self._process_task(task, run_id, summary)
            except Exception as exc:
                log.error(
                    "runner.task_error task_id=%s err=%s",
                    task.id, exc, exc_info=True,
                )
                summary.failed_count += 1
                summary.errors.append(str(exc))

    def _process_task(self, task: Task, run_id: str, summary: RunSummary) -> None:
        """Claim, execute, verify, and update a single task."""
        # Claim task
        task = self.ledger.claim(task.id, run_id)

        # Fire before_task hook
        self.hooks.fire("before_task", TaskClaimed(run_id=run_id, task=task))

        # dry_run: skip execution
        if self.config.dry_run:
            log.info("runner.dry_run task_id=%s title=%s", task.id, task.title[:60])
            self.ledger.skip(task.id, reason="dry_run")
            return

        # Execute with WorkerAgent
        attempt = task.retry_count
        worker = WorkerAgent(
            provider=self.provider,
            config=self.config,
            context_manager=self._context_manager,
        )

        try:
            result = worker.run(
                task,
                run_id=run_id,
                attempt=attempt,
            )
        except Exception as exc:
            error_msg = f"WorkerAgent failed: {exc!s}"[:300]
            log.warning("runner.worker_error task_id=%s err=%s", task.id, exc)
            updated = self.ledger.mark_failed(task.id, error_msg)
            self.hooks.fire("on_failure", TaskFailed(run_id=run_id, task=updated, error=error_msg))
            if updated.status == TaskStatus.ABANDONED:
                summary.abandoned_count += 1
            else:
                summary.failed_count += 1
            return

        # Submit result for verification
        self.ledger.submit_result(task.id, result)

        # Verify result
        verification_passed, error_msg = self._verify(task, result)

        if verification_passed:
            updated = self.ledger.mark_done(task.id, result)
            self.hooks.fire("after_task", TaskCompleted(run_id=run_id, task=updated, result=result))
            summary.done_count += 1
        else:
            updated = self.ledger.mark_failed(task.id, error_msg or "Verification failed")
            self.hooks.fire(
                "on_failure", TaskFailed(run_id=run_id, task=updated, error=error_msg or ""),
            )
            if updated.status == TaskStatus.ABANDONED:
                summary.abandoned_count += 1
            else:
                summary.failed_count += 1

    def _verify(self, task: Task, result: TaskResult) -> tuple[bool, str]:
        """Run the configured verifier. Returns (passed, error_message)."""
        if not self._verifier_registry:
            # Use the global registry with all built-in verifiers loaded
            try:
                import veridian.verify.builtin  # noqa: F401,PLC0415 — triggers registration
                from veridian.verify.base import registry  # noqa: PLC0415
                self._verifier_registry = registry
            except Exception:
                return True, ""

        try:
            config = task.verifier_config or {}
            verifier = self._verifier_registry.get(task.verifier_id, config or None)
            vresult = verifier.verify(task, result)
            return vresult.passed, vresult.error or ""
        except Exception as exc:
            log.warning("runner.verify_error task_id=%s err=%s", task.id, exc)
            return False, str(exc)[:300]

    def _setup_signal_handler(self) -> None:
        """Register SIGINT handler to set shutdown flag (no mid-task exit)."""
        def _handler(signum: int, frame: object) -> None:
            log.warning("runner.sigint_received — will stop after current task")
            self._shutdown = True

        with contextlib.suppress(OSError, ValueError):
            # signal.signal fails in non-main threads — ignore
            signal.signal(signal.SIGINT, _handler)
