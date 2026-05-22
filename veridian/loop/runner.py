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
"""

from __future__ import annotations

import contextlib
import logging
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.context.manager import ContextManager
from veridian.context.window import TokenWindow
from veridian.core.config import VeridianConfig
from veridian.core.events import (
    RunCompleted,
    RunStarted,
    TaskClaimed,
    TaskCompleted,
    TaskFailed,
    TaskPaused,
    TaskResumed,
)
from veridian.core.exceptions import ControlFlowSignal, HumanReviewRequired, TaskPauseRequested
from veridian.core.task import (
    Task,
    TaskResult,
    TaskStatus,
    TraceStep,
)
from veridian.hooks.registry import HookRegistry
from veridian.loop.replay_compat import (
    build_run_replay_snapshot,
    check_replay_compatibility,
)
from veridian.loop.runtime_store import RuntimeStore
from veridian.loop.worker import WorkerAgent
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
        self._shutdown = False
        self._run_id = str(uuid.uuid4())[:8]
        # Context manager for worker prompt assembly
        self._context_manager = ContextManager(
            window=TokenWindow(capacity=self.config.context_window_tokens),
            provider=provider,
            progress_path=Path(str(self.config.progress_file)),
        )
        # Observability: wire env-configured trace + alert hooks before any
        # event fires. Safe no-op when none of the env vars are set.
        from veridian.observability.setup import auto_register  # noqa: PLC0415

        auto_register(self.hooks)

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
        # ------ Step 1: Crash recovery --- ALWAYS FIRST ------------------------------------------------------------------------------------
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

        # ------ Step 2: RunStarted hook ---------------------------------------------------------------------------------------------------------------------------------
        self.hooks.fire(
            "before_run",
            RunStarted(run_id=run_id, total_tasks=summary.total_tasks, phase=phase),
        )

        # Save the previous SIGINT handler so the runner does not leak its
        # own handler into the caller's process. Restoration happens in
        # ``finally`` to cover the exception paths too.
        previous_sigint = self._setup_signal_handler()

        try:
            # ------ Step 3: Task loop ---------------------------------------------------------------------------------------------------------------------------------------
            self._task_loop(run_id, phase, summary)

            # ------ Step 4: RunCompleted hook ---------------------------------------------------------------------------------------------------------------
            summary.duration_seconds = time.monotonic() - start_time
            self.hooks.fire(
                "after_run",
                RunCompleted(run_id=run_id, summary=summary),
            )

            log.info(
                "runner.complete run_id=%s done=%d failed=%d duration=%.1fs",
                run_id,
                summary.done_count,
                summary.failed_count,
                summary.duration_seconds,
            )
            return summary
        finally:
            self._restore_signal_handler(previous_sigint)

    def _task_loop(
        self,
        run_id: str,
        phase: str | None,
        summary: RunSummary,
    ) -> None:
        """Inner loop: process tasks until queue empty or shutdown signalled.

        RV3-001: When ``config.resume_paused_on_start`` is True, PAUSED tasks are
        surfaced before new PENDING work and resumed via ``ledger.resume()``.
        Tasks paused during the current run are recorded so they are not
        re-resumed this run (preventing a pause---resume---pause infinite loop when
        the pausing hook is still in effect --- the operator must remove the
        pause condition before the next run).
        """
        include_paused = bool(getattr(self.config, "resume_paused_on_start", True))
        paused_this_run: set[str] = set()
        while not self._shutdown:
            task = self.ledger.get_next(phase=phase, include_paused=include_paused)
            if task is None:
                break

            # Skip tasks we already paused in this run --- operator intervention
            # is required before the same pause condition can resolve.
            if task.id in paused_this_run:
                # First try another PAUSED task we haven't attempted in this run.
                # This avoids starvation where one repeatedly-paused task blocks
                # all other paused work from resuming.
                other_paused = self.ledger.list(status=TaskStatus.PAUSED)
                if phase:
                    other_paused = [t for t in other_paused if t.phase == phase]
                next_paused = next((t for t in other_paused if t.id not in paused_this_run), None)
                if next_paused is not None:
                    task = next_paused
                else:
                    # No resumable paused candidates left this run; fall back to
                    # fresh PENDING work only.
                    task = self.ledger.get_next(phase=phase, include_paused=False)
                    if task is None:
                        break

            # RV3-001: If this is a PAUSED task, resume it before dispatch.
            if task.status == TaskStatus.PAUSED:
                try:
                    task = self.ledger.resume(task.id, run_id)
                except Exception as exc:
                    log.warning("runner.resume_failed task_id=%s err=%s", task.id, exc)
                    summary.errors.append(f"resume_failed: {exc}")
                    continue
                resume_count = 0
                if task.result is not None:
                    resume_count = int(
                        task.result.extras.get("pause_payload", {}).get("resume_count", 0)
                    )
                try:
                    self.hooks.fire(
                        "on_resume",
                        TaskResumed(run_id=run_id, task=task, resume_count=resume_count),
                    )
                except ControlFlowSignal as signal:
                    # RV3-002 hardening: on_resume is part of control flow and can
                    # intentionally request another pause. Route it through the
                    # same pause persistence path as before_task signals.
                    self._handle_pause_signal(task, run_id, signal, summary)
                    paused_this_run.add(task.id)
                    continue

            try:
                self._process_task(task, run_id, summary)
            except ControlFlowSignal as signal:
                # RV3-001/002: control-flow signals (HumanReviewRequired,
                # TaskPauseRequested) are routed to ledger.pause() so the task
                # is preserved across restarts. DO NOT count as failure.
                self._handle_pause_signal(task, run_id, signal, summary)
                paused_this_run.add(task.id)
            except Exception as exc:
                log.error(
                    "runner.task_error task_id=%s err=%s",
                    task.id,
                    exc,
                    exc_info=True,
                )
                summary.failed_count += 1
                summary.errors.append(str(exc))

    def _handle_pause_signal(
        self,
        task: Task,
        run_id: str,
        signal: ControlFlowSignal,
        summary: RunSummary,
    ) -> None:
        """RV3-001: Transition a task to PAUSED and fire the TaskPaused event.

        The runner was mid-execution when a hook (or nested code) raised a
        ControlFlowSignal. We must:
          1. Call ledger.pause() with the signal's reason + payload.
          2. Fire the TaskPaused event so hooks see it.
          3. NOT increment done_count or failed_count --- paused is a neutral
             outcome that resumes next run.
        """
        reason = ""
        payload: dict[str, Any] = {}
        if isinstance(signal, TaskPauseRequested):
            reason = signal.reason
            payload = dict(signal.payload)
        elif isinstance(signal, HumanReviewRequired):
            reason = str(signal)
            payload = {"resume_hint": "Human approval granted"}
        else:
            reason = str(signal) or type(signal).__name__

        try:
            paused_task = self.ledger.pause(task.id, reason=reason, payload=payload)
        except Exception as exc:
            log.error("runner.pause_persist_failed task_id=%s err=%s", task.id, exc)
            summary.failed_count += 1
            summary.errors.append(f"pause_persist_failed: {exc}")
            return

        self.hooks.fire(
            "on_pause",
            TaskPaused(
                run_id=run_id,
                task=paused_task,
                reason=reason,
                payload=payload,
            ),
        )
        log.info("runner.task_paused task_id=%s reason=%s", task.id, reason[:80])

    def _process_task(self, task: Task, run_id: str, summary: RunSummary) -> None:
        """Claim, execute, verify, and update a single task."""
        task = self.ledger.claim(task.id, run_id)
        self.hooks.fire("before_task", TaskClaimed(run_id=run_id, task=task))

        if self.config.dry_run:
            log.info("runner.dry_run task_id=%s title=%s", task.id, task.title[:60])
            self.ledger.skip(task.id, reason="dry_run")
            return

        worker = WorkerAgent(
            provider=self.provider,
            config=self.config,
            context_manager=self._context_manager,
        )

        resume_result = task.result if isinstance(task.result, TaskResult) else None
        current_replay_snapshot = build_run_replay_snapshot(task, self.provider)
        if resume_result is not None and bool(getattr(self.config, "strict_replay", False)):
            saved_snap = resume_result.extras.get("run_replay_snapshot")
            if isinstance(saved_snap, dict):
                drift_error = check_replay_compatibility(
                    task=task,
                    current=current_replay_snapshot,
                    saved=saved_snap,
                    strict=True,
                )
                if drift_error:
                    updated = self.ledger.mark_failed(task.id, drift_error)
                    self.hooks.fire(
                        "on_failure",
                        TaskFailed(run_id=run_id, task=updated, error=drift_error),
                    )
                    if updated.status == TaskStatus.ABANDONED:
                        summary.abandoned_count += 1
                    else:
                        summary.failed_count += 1
                    return

        try:
            result = worker.run(
                task,
                run_id=run_id,
                run_summary="",
                attempt=task.retry_count,
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

        if resume_result is not None and resume_result.extras:
            for key, value in resume_result.extras.items():
                result.extras.setdefault(key, value)

        result.extras["run_replay_snapshot"] = current_replay_snapshot.to_dict()
        self._namespace_trace_steps(result.trace_steps, attempt_number=1)
        verification_passed, error_msg, verify_meta = self._verify(task, result)
        result.verifier_score = verify_meta.get("score")
        result.verification_evidence = verify_meta.get("evidence", {})
        if verify_meta.get("verification_ms") is not None:
            result.timing["verification_ms"] = verify_meta["verification_ms"]
        result.trace_steps.append(
            TraceStep(
                step_id=f"verify_{len(result.trace_steps) + 1}",
                role="verifier",
                action_type="verify",
                content="passed"
                if verification_passed
                else f"failed: {error_msg or 'verification failed'}",
                timestamp_ms=int(time.time() * 1000),
                latency_ms=int(verify_meta.get("verification_ms", 0) or 0),
                metadata={"verifier_id": task.verifier_id},
            )
        )
        result.confidence = self._build_confidence(task, verify_meta)

        self.ledger.submit_result(task.id, result)
        if verification_passed:
            updated = self.ledger.mark_done(task.id, result)
            self.hooks.fire("after_task", TaskCompleted(run_id=run_id, task=updated, result=result))
            summary.done_count += 1
        else:
            updated = self.ledger.mark_failed(task.id, error_msg or "Verification failed")
            self.hooks.fire(
                "on_failure",
                TaskFailed(run_id=run_id, task=updated, error=error_msg or ""),
            )
            if updated.status == TaskStatus.ABANDONED:
                summary.abandoned_count += 1
            else:
                summary.failed_count += 1

    def _verify(self, task: Task, result: TaskResult) -> tuple[bool, str, dict[str, Any]]:
        """Run verifier and return (passed, error_message, verify_meta)."""
        verify_start = time.perf_counter()
        if not self._verifier_registry:
            try:
                from veridian.verify.base import registry  # noqa: PLC0415

                self._verifier_registry = registry
            except Exception:
                return (
                    True,
                    "",
                    {
                        "score": None,
                        "evidence": {},
                        "verification_ms": round((time.perf_counter() - verify_start) * 1000, 1),
                    },
                )

        try:
            config = task.verifier_config or {}
            verifier = self._verifier_registry.get(task.verifier_id, config or None)
            vresult = verifier.verify(task, result)
            return (
                vresult.passed,
                vresult.error or "",
                {
                    "score": vresult.score,
                    "evidence": vresult.evidence or {},
                    "verification_ms": round((time.perf_counter() - verify_start) * 1000, 1),
                },
            )
        except Exception as exc:
            log.warning("runner.verify_error task_id=%s err=%s", task.id, exc)
            return (
                False,
                str(exc)[:300],
                {
                    "score": None,
                    "evidence": {"verify_error": str(exc)[:300]},
                    "verification_ms": round((time.perf_counter() - verify_start) * 1000, 1),
                },
            )

    def _build_confidence(
        self,
        task: Task,
        verify_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build normalized confidence envelope from retry and verifier metadata."""
        verifier_score = verify_meta.get("score")
        evidence = verify_meta.get("evidence", {})
        consistency_score = None
        if isinstance(evidence, dict):
            raw_consistency = evidence.get("consistency_score")
            if isinstance(raw_consistency, (int, float)):
                consistency_score = float(raw_consistency)

        try:
            from veridian.verify.builtin.confidence import ConfidenceScore  # noqa: PLC0415

            score = ConfidenceScore.compute(
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                verifier_score=float(verifier_score)
                if isinstance(verifier_score, (int, float))
                else None,
                consistency_score=consistency_score,
            )
            out: dict[str, Any] = score.to_dict()
            return out
        except Exception:
            fallback = max(0.1, 1.0 - (task.retry_count * 0.25))
            return {
                "composite": round(fallback, 3),
                "tier": "LOW" if fallback < 0.65 else "MEDIUM",
            }

    def _namespace_trace_steps(self, trace_steps: list[TraceStep], attempt_number: int) -> None:
        """Ensure trace step IDs remain unique across repair attempts."""
        for idx, step in enumerate(trace_steps, start=1):
            base_id = step.step_id or f"step_{idx}"
            step.step_id = f"a{attempt_number}_{idx}_{base_id}"

    def _setup_signal_handler(self) -> object:
        """Register SIGINT handler to set shutdown flag (no mid-task exit).

        Returns the previously-installed handler so callers can restore it
        when the run finishes. Returns ``None`` when handler installation
        was not possible (e.g. running in a non-main thread).
        """

        def _handler(signum: int, frame: object) -> None:
            log.warning("runner.sigint_received --- will stop after current task")
            self._shutdown = True

        try:
            return signal.signal(signal.SIGINT, _handler)
        except (OSError, ValueError):
            # signal.signal fails in non-main threads --- return sentinel
            return None

    def _restore_signal_handler(self, previous: object) -> None:
        """Restore a previously-installed SIGINT handler.

        Idempotent and safe to call from a ``finally`` block: if
        ``_setup_signal_handler`` returned ``None`` (non-main thread, etc.)
        this is a no-op.
        """
        if previous is None:
            return
        with contextlib.suppress(OSError, ValueError, TypeError):
            signal.signal(signal.SIGINT, previous)  # type: ignore[arg-type]
