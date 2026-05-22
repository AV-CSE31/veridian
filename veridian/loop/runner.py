"""
veridian.loop.runner
---------------------------------------------------------------
VeridianRunner --- the main synchronous task execution loop.

Runner sequence (FROZEN - do NOT reorder):
  1. reset_in_progress()        --- crash recovery, ALWAYS first
  2. fire RunStarted hook
  3. Loop: get_next() --- claim --- worker --- verify --- mark_done/failed
  4. fire RunCompleted / RunAborted hook
  5. Return RunSummary

SIGINT contract:
  - Set _shutdown flag
  - Finish current task
  - Write RunSummary
  - Exit cleanly --- never sys.exit() mid-task

dry_run=True:
  - Assemble context, log what would run, return RunSummary(dry_run=True)
  - Never calls provider.complete()

Architecture (Section C of the audit):
  - VeridianRunner is the composition root and the only public API.
  - _RunController (loop/run_controller.py) owns lifecycle + signal handling.
  - _TaskDispatcher (loop/task_dispatcher.py) owns the per-task loop.
  - VeridianRunner exposes thin _task_loop / _verifier_registry shims so
    pre-existing tests that mock these attributes keep working.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.context.manager import ContextManager
from veridian.context.window import TokenWindow
from veridian.core.config import VeridianConfig
from veridian.core.task import TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.loop.run_controller import _RunController
from veridian.loop.runtime_store import RuntimeStore
from veridian.loop.task_dispatcher import _TaskDispatcher
from veridian.providers.base import LLMProvider
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
        if verifier_registry is None:
            from veridian.verify.base import registry as _builtin_registry  # noqa: PLC0415

            self._verifier_registry = _builtin_registry
        else:
            self._verifier_registry = verifier_registry
        self._run_id = str(uuid.uuid4())[:8]
        # Context manager for worker prompt assembly
        self._context_manager = ContextManager(
            window=TokenWindow(capacity=self.config.context_window_tokens),
            provider=provider,
            progress_path=Path(str(self.config.progress_file)),
        )
        # Lifecycle + dispatch helpers. Package-private; tests should mock
        # the VeridianRunner.* methods that delegate to them.
        self._controller = _RunController(hooks=self.hooks)
        self._dispatcher = _TaskDispatcher(
            ledger=self.ledger,
            provider=self.provider,
            config=self.config,
            hooks=self.hooks,
            context_manager=self._context_manager,
            verifier_registry=self._verifier_registry,
            controller=self._controller,
        )
        # Observability: wire env-configured trace + alert hooks before any
        # event fires. Safe no-op when none of the env vars are set.
        from veridian.observability.setup import auto_register  # noqa: PLC0415

        auto_register(self.hooks)

    # Back-compat shim: tests assert that runner._shutdown reflects the
    # controller's flag.
    @property
    def _shutdown(self) -> bool:
        return self._controller.shutdown

    @_shutdown.setter
    def _shutdown(self, value: bool) -> None:
        if value:
            self._controller.request_shutdown()
        else:
            self._controller._shutdown = False  # explicit reset for tests

    def run(self, phase: str | None = None) -> RunSummary:
        """
        Execute all pending tasks in the ledger.

        RUNNER SEQUENCE (frozen):
          1. reset_in_progress()
          2. fire RunStarted
          3. Task loop
          4. fire RunCompleted
          5. Return RunSummary
        """
        start_time = time.monotonic()
        run_id = self._run_id
        phase = phase or self.config.phase

        # Pin a trace id for the current execution context. Logs and
        # JsonlTraceHook records both pick this up automatically.
        from veridian.observability.trace import set_trace_id  # noqa: PLC0415

        set_trace_id(run_id)

        summary = RunSummary(
            run_id=run_id,
            dry_run=self.config.dry_run,
            phase=phase,
        )
        # ------ Step 1: Crash recovery --- ALWAYS FIRST -----------------------
        self.ledger.reset_in_progress()

        # Count total schedulable tasks. RV3-001: when resume_paused_on_start
        # is enabled, PAUSED tasks also count so the runner doesn't short-circuit
        # when the only work is resume candidates.
        pending = self.ledger.list(status=TaskStatus.PENDING)
        if phase:
            pending = [t for t in pending if t.phase == phase]
        schedulable_count = len(pending)
        if bool(getattr(self.config, "resume_paused_on_start", True)):
            paused = self.ledger.list(status=TaskStatus.PAUSED)
            if phase:
                paused = [t for t in paused if t.phase == phase]
            schedulable_count += len(paused)
        summary.total_tasks = schedulable_count

        if summary.total_tasks == 0:
            log.info("runner.no_tasks run_id=%s phase=%s", run_id, phase)
            summary.duration_seconds = time.monotonic() - start_time
            return summary

        # ------ Step 2: RunStarted hook ---------------------------------------
        self._controller.fire_run_started(run_id, summary.total_tasks, phase)

        # Save the previous SIGINT handler so the runner does not leak its
        # own handler into the caller's process. Restoration happens in
        # ``finally`` to cover the exception paths too.
        self._controller.install_signal_handler()

        try:
            # ------ Step 3: Task loop -----------------------------------------
            self._task_loop(run_id, phase, summary)

            # ------ Step 4: RunCompleted hook ---------------------------------
            summary.duration_seconds = time.monotonic() - start_time
            self._controller.fire_run_completed(run_id, summary)

            log.info(
                "runner.complete run_id=%s done=%d failed=%d duration=%.1fs",
                run_id,
                summary.done_count,
                summary.failed_count,
                summary.duration_seconds,
            )
            return summary
        finally:
            self._controller.restore_signal_handler()

    # ------ delegating shims (preserve test compatibility) ---------------
    def _task_loop(self, run_id: str, phase: str | None, summary: RunSummary) -> None:
        """Delegate the inner loop to :class:`_TaskDispatcher`.

        Kept as a method on VeridianRunner so existing tests that mock
        ``runner._task_loop`` (e.g. test_phase1b_resource_lifecycle) continue
        to work without modification.
        """
        self._dispatcher.run_loop(run_id, phase, summary)

    def _handle_pause_signal(self, *args: Any, **kwargs: Any) -> None:
        self._dispatcher._handle_pause_signal(*args, **kwargs)

    def _process_task(self, *args: Any, **kwargs: Any) -> None:
        self._dispatcher._process_task(*args, **kwargs)

    def _verify(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return self._dispatcher._verify(*args, **kwargs)
