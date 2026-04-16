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
import hashlib
import json
import logging
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.agents.worker import WorkerAgent
from veridian.context.manager import ContextManager
from veridian.context.window import TokenWindow
from veridian.contracts.prm_policy import PRMPolicyConfig, evaluate_prm_policy
from veridian.core.config import VeridianConfig
from veridian.core.dlq import DeadLetterQueue
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
    PRMBudget,
    PRMRunResult,
    PRMScore,
    Task,
    TaskResult,
    TaskStatus,
    TraceStep,
)
from veridian.hooks.registry import HookRegistry
from veridian.loop.activity import ActivityJournal
from veridian.loop.checkpoint_cursor import CheckpointCursorError, advance_cursor
from veridian.loop.replay_compat import (
    build_run_replay_snapshot,
    check_replay_compatibility,
)
from veridian.loop.runtime_store import RuntimeStore
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
        ledger: RuntimeStore,
        provider: LLMProvider,
        config: VeridianConfig | None = None,
        hooks: HookRegistry | None = None,
        verifier_registry: VerifierRegistry | None = None,
        skill_library: SkillLibrary | None = None,
        dlq: DeadLetterQueue | None = None,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.config = config or VeridianConfig()
        self.hooks = hooks or HookRegistry()
        self._verifier_registry = verifier_registry
        self._shutdown = False
        self._run_id = str(uuid.uuid4())[:8]
        # Audit F1: DLQ for abandoned tasks — opt-in; None = disabled.
        self._dlq = dlq
        self._tracer: Any | None = None
        self._prm_backend_failures = 0
        self._prm_circuit_open = False
        self._prm_circuit_threshold = 3
        if self.config.trace_file:
            with contextlib.suppress(Exception):
                from veridian.observability.tracer import VeridianTracer  # noqa: PLC0415

                self._tracer = VeridianTracer(trace_file=Path(str(self.config.trace_file)))

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
        if self._tracer is not None:
            with contextlib.suppress(Exception):
                self._tracer.start_trace(
                    run_id=run_id,
                    attributes={"veridian.phase": phase or "", "veridian.runner": "sync"},
                )

        # ── Step 1: Crash recovery — ALWAYS FIRST ────────────────────────────
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
            if self._tracer is not None:
                with contextlib.suppress(Exception):
                    self._tracer.end_trace(attributes={"veridian.total_tasks": 0})
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
            run_id,
            summary.done_count,
            summary.failed_count,
            summary.duration_seconds,
        )
        if self._tracer is not None:
            with contextlib.suppress(Exception):
                self._tracer.end_trace(
                    attributes={
                        "veridian.done_count": summary.done_count,
                        "veridian.failed_count": summary.failed_count,
                        "veridian.abandoned_count": summary.abandoned_count,
                    }
                )
        return summary

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
        re-resumed this run (preventing a pause→resume→pause infinite loop when
        the pausing hook is still in effect — the operator must remove the
        pause condition before the next run).
        """
        include_paused = bool(getattr(self.config, "resume_paused_on_start", True))
        paused_this_run: set[str] = set()
        while not self._shutdown:
            task = self.ledger.get_next(phase=phase, include_paused=include_paused)
            if task is None:
                break

            # Skip tasks we already paused in this run — operator intervention
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
          2. Fire the TaskPaused event so observability hooks see it.
          3. NOT increment done_count or failed_count — paused is a neutral
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

    def _enqueue_dlq(self, task: Task, error: str) -> None:
        """Audit F1: Route abandoned tasks into the Dead Letter Queue when one
        is attached. Best-effort — DLQ failures are logged but never propagate."""
        if self._dlq is None:
            return
        try:
            self._dlq.enqueue(
                task=task,
                failure_reason=error,
                retry_count=task.retry_count,
                extra={"last_error": task.last_error},
            )
        except Exception as exc:
            log.warning("runner.dlq_enqueue_failed task_id=%s err=%s", task.id, exc)

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

        # RV3-004/005: seed the activity journal from any previous run's
        # TaskResult.extras so cached LLM outputs are returned on replay.
        activity_journal: ActivityJournal | None = None
        if bool(getattr(self.config, "activity_journal_enabled", False)):
            previous_journal = (
                task.result.extras.get("activity_journal")
                if isinstance(task.result, TaskResult)
                else None
            )
            if isinstance(previous_journal, list):
                activity_journal = ActivityJournal.from_list(previous_journal)
            else:
                activity_journal = ActivityJournal()

        worker = WorkerAgent(
            provider=self.provider,
            config=self.config,
            context_manager=self._context_manager,
            activity_journal=activity_journal,
        )

        resume_result = task.result if isinstance(task.result, TaskResult) else None
        checkpoint = self._get_prm_checkpoint(resume_result)
        replay_incompatible_error = self._check_prm_replay_compatibility(task, resume_result)
        if replay_incompatible_error:
            updated = self.ledger.mark_failed(task.id, replay_incompatible_error)
            self.hooks.fire(
                "on_failure",
                TaskFailed(run_id=run_id, task=updated, error=replay_incompatible_error),
            )
            if updated.status == TaskStatus.ABANDONED:
                self._enqueue_dlq(updated, updated.last_error or "")
                summary.abandoned_count += 1
            else:
                summary.failed_count += 1
            return

        # RV3-003: Global replay compatibility envelope — applies to every task,
        # not just PRM-enabled ones. Fails closed in strict mode when the
        # model/prompt/verifier_config has drifted from the saved snapshot.
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
                        self._enqueue_dlq(updated, updated.last_error or "")
                        summary.abandoned_count += 1
                    else:
                        summary.failed_count += 1
                    return

        # If a previous run checkpoint already completed verification+policy, finalize without re-run.
        if resume_result is not None and checkpoint.get("ready_to_finalize"):
            checkpoint_passed = bool(checkpoint.get("last_verification_passed", False))
            checkpoint_error = str(checkpoint.get("last_error", "") or "")
            policy_action = str(
                (resume_result.prm_result.policy_action if resume_result.prm_result else "")
                or checkpoint.get("last_policy_action", "")
            )
            if policy_action == "block":
                checkpoint_passed = False
                if not checkpoint_error:
                    checkpoint_error = "Blocked by PRM policy (checkpoint replay)."

            self.ledger.submit_result(task.id, resume_result)
            if checkpoint_passed:
                updated = self.ledger.mark_done(task.id, resume_result)
                self.hooks.fire(
                    "after_task",
                    TaskCompleted(run_id=run_id, task=updated, result=resume_result),
                )
                summary.done_count += 1
            else:
                updated = self.ledger.mark_failed(
                    task.id,
                    checkpoint_error or "Verification failed (checkpoint replay)",
                )
                self.hooks.fire(
                    "on_failure",
                    TaskFailed(
                        run_id=run_id,
                        task=updated,
                        error=checkpoint_error or "Verification failed (checkpoint replay)",
                    ),
                )
                if updated.status == TaskStatus.ABANDONED:
                    self._enqueue_dlq(updated, updated.last_error or "")
                    summary.abandoned_count += 1
                else:
                    summary.failed_count += 1
            return

        repair_attempts = int(checkpoint.get("repair_attempts", 0))
        repair_note = ""
        aggregated_trace_steps: list[TraceStep] = (
            list(resume_result.trace_steps) if resume_result is not None else []
        )
        aggregated_tool_calls: list[Any] = (
            list(resume_result.tool_calls) if resume_result is not None else []
        )
        total_input_tokens = (
            int(resume_result.token_usage.get("input_tokens", 0) or 0) if resume_result else 0
        )
        total_output_tokens = (
            int(resume_result.token_usage.get("output_tokens", 0) or 0) if resume_result else 0
        )
        total_worker_ms = (
            float(resume_result.timing.get("worker_ms", 0.0) or 0.0) if resume_result else 0.0
        )
        total_worker_turns = (
            int(resume_result.timing.get("worker_turns", 0) or 0) if resume_result else 0
        )
        prm_accumulator: PRMRunResult | None = resume_result.prm_result if resume_result else None

        while True:
            execution_attempt = attempt + repair_attempts
            try:
                result = worker.run(
                    task,
                    run_id=run_id,
                    run_summary=repair_note,
                    attempt=execution_attempt,
                )
            except Exception as exc:
                error_msg = f"WorkerAgent failed: {exc!s}"[:300]
                log.warning("runner.worker_error task_id=%s err=%s", task.id, exc)
                updated = self.ledger.mark_failed(task.id, error_msg)
                self.hooks.fire(
                    "on_failure", TaskFailed(run_id=run_id, task=updated, error=error_msg)
                )
                if updated.status == TaskStatus.ABANDONED:
                    self._enqueue_dlq(updated, updated.last_error or "")
                    summary.abandoned_count += 1
                else:
                    summary.failed_count += 1
                return

            # RV3-001: Preserve pause_payload and other extras from a resumed
            # task's checkpoint so audit fields (resume_count, paused_at, ...)
            # survive into the final TaskResult even though the worker produced
            # a fresh TaskResult object.
            if resume_result is not None and resume_result.extras:
                for key, value in resume_result.extras.items():
                    result.extras.setdefault(key, value)

            # RV3-003: Persist the current replay snapshot so subsequent runs
            # can fail closed on model/prompt/verifier-config drift. Always
            # overwrite — this run's snapshot IS the authoritative baseline
            # for the next run.
            result.extras["run_replay_snapshot"] = current_replay_snapshot.to_dict()

            # RV3-004/005: Persist the activity journal so resumed runs can
            # return cached side-effect outputs and avoid duplicate LLM calls.
            if activity_journal is not None:
                result.extras["activity_journal"] = activity_journal.to_list()

            # WCP-011: Advance the deterministic checkpoint cursor. Each
            # worker attempt is one logical step; subsequent verify/policy
            # passes stamp their own cursors below. Cross-task violations
            # from a resumed stale result are surfaced, not swallowed.
            try:
                last_activity_key = (
                    activity_journal.records[-1].idempotency_key
                    if (activity_journal is not None and len(activity_journal) > 0)
                    else ""
                )
                cursor_step_id = (
                    result.trace_steps[-1].step_id
                    if result.trace_steps
                    else f"worker_attempt_{repair_attempts + 1}"
                )
                advance_cursor(
                    result=result,
                    task_id=task.id,
                    step_id=cursor_step_id,
                    activity_key=last_activity_key,
                    state={
                        "attempt": attempt,
                        "repair_attempts": repair_attempts,
                        "verifier_id": task.verifier_id,
                    },
                    metadata={"phase": "worker"},
                )
            except CheckpointCursorError as exc:
                log.warning("runner.cursor_advance_failed task_id=%s err=%s", task.id, exc)

            self._namespace_trace_steps(result.trace_steps, attempt_number=repair_attempts + 1)
            aggregated_trace_steps.extend(result.trace_steps)
            result.trace_steps = list(aggregated_trace_steps)
            aggregated_tool_calls.extend(result.tool_calls)
            result.tool_calls = list(aggregated_tool_calls)
            total_input_tokens += int(result.token_usage.get("input_tokens", 0) or 0)
            total_output_tokens += int(result.token_usage.get("output_tokens", 0) or 0)
            result.token_usage = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            }
            total_worker_ms += float(result.timing.get("worker_ms", 0.0) or 0.0)
            total_worker_turns += int(result.timing.get("worker_turns", 0) or 0)
            result.timing["worker_ms"] = round(total_worker_ms, 1)
            result.timing["worker_turns"] = total_worker_turns
            result.prm_result = prm_accumulator
            self._persist_prm_checkpoint(task.id, result, repair_attempts=repair_attempts)

            # WCP-010: expose in-memory journal reference for verifier
            # activity boundaries (e.g., HttpStatusVerifier) during this verify
            # call only; the reference is removed before persistence.
            if activity_journal is not None:
                result.extras["_activity_journal_ref"] = activity_journal
            try:
                # Verify result
                verification_passed, error_msg, verify_meta = self._verify(task, result)
            finally:
                result.extras.pop("_activity_journal_ref", None)
            result.verifier_score = verify_meta.get("score")
            result.verification_evidence = verify_meta.get("evidence", {})
            if verify_meta.get("verification_ms") is not None:
                result.timing["verification_ms"] = verify_meta["verification_ms"]
            result.trace_steps.append(
                TraceStep(
                    step_id=f"a{repair_attempts + 1}_verify_{len(result.trace_steps) + 1}",
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

            # PRM policy lifecycle (optional; driven by task.metadata["prm"])
            prm_action, prm_reason, prm_repair_hint = self._apply_prm_policy(
                task=task,
                result=result,
                repair_attempts=repair_attempts,
            )
            prm_accumulator = result.prm_result
            result.confidence = self._build_confidence(task, verify_meta, result.prm_result)
            if prm_action is not None or result.prm_result is not None:
                self._record_prm_policy_checkpoint(
                    result=result,
                    action=prm_action,
                    reason=prm_reason,
                    repair_attempts=repair_attempts,
                )
                self._set_prm_checkpoint_outcome(
                    result=result,
                    verification_passed=verification_passed,
                    error=error_msg,
                    policy_action=prm_action,
                    ready_to_finalize=prm_action != "retry_with_repair",
                    repair_attempts=repair_attempts,
                )
                self._persist_prm_checkpoint(task.id, result, repair_attempts=repair_attempts)

            if prm_action == "retry_with_repair":
                repair_attempts += 1
                repair_note = (
                    "[PRM_REPAIR_ATTEMPT]\n"
                    f"{prm_repair_hint or prm_reason or 'Improve reasoning quality and retry.'}"
                )
                self._record_prm_event(
                    "veridian.prm.repair_attempt",
                    {
                        "task.id": task.id,
                        "run.id": run_id,
                        "prm.repair_attempt": repair_attempts,
                        "prm.reason": prm_reason or "",
                    },
                )
                result.trace_steps.append(
                    TraceStep(
                        step_id=f"a{repair_attempts}_repair_{len(result.trace_steps) + 1}",
                        role="planner",
                        action_type="plan",
                        content=repair_note,
                        timestamp_ms=int(time.time() * 1000),
                        metadata={"repair_attempt": repair_attempts},
                    )
                )
                aggregated_trace_steps = list(result.trace_steps)
                if prm_action is not None or result.prm_result is not None:
                    self._set_prm_checkpoint_outcome(
                        result=result,
                        verification_passed=False,
                        error=error_msg,
                        policy_action=prm_action,
                        ready_to_finalize=False,
                        repair_attempts=repair_attempts,
                    )
                    self._persist_prm_checkpoint(task.id, result, repair_attempts=repair_attempts)
                continue

            if prm_action == "block":
                verification_passed = False
                error_msg = (prm_reason or "Blocked by PRM policy")[:300]
            break

        # Submit only final result so ledger state transitions remain valid.
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
                self._enqueue_dlq(updated, updated.last_error or "")
                summary.abandoned_count += 1
            else:
                summary.failed_count += 1

    def _verify(self, task: Task, result: TaskResult) -> tuple[bool, str, dict[str, Any]]:
        """Run verifier and return (passed, error_message, verify_meta)."""
        verify_start = time.perf_counter()
        if not self._verifier_registry:
            # Use the global registry with all built-in verifiers loaded
            try:
                import veridian.verify.builtin  # noqa: F401,PLC0415 — triggers registration
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
        prm_result: PRMRunResult | None = None,
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
            if prm_result is not None:
                out["prm_score"] = round(float(prm_result.aggregate_score), 3)
                out["prm_confidence"] = round(float(prm_result.aggregate_confidence), 3)
                composite = out.get("composite")
                if isinstance(composite, (int, float)):
                    out["composite"] = round(
                        min(
                            float(composite),
                            float(prm_result.aggregate_score),
                            float(prm_result.aggregate_confidence),
                        ),
                        3,
                    )
            return out
        except Exception:
            fallback = max(0.1, 1.0 - (task.retry_count * 0.25))
            fallback_composite = round(fallback, 3)
            out = {
                "composite": fallback_composite,
                "tier": "LOW" if fallback < 0.65 else "MEDIUM",
            }
            if prm_result is not None:
                out["prm_score"] = round(float(prm_result.aggregate_score), 3)
                out["prm_confidence"] = round(float(prm_result.aggregate_confidence), 3)
                out["composite"] = round(
                    min(
                        float(fallback_composite),
                        float(prm_result.aggregate_score),
                        float(prm_result.aggregate_confidence),
                    ),
                    3,
                )
            return out

    def _namespace_trace_steps(self, trace_steps: list[TraceStep], attempt_number: int) -> None:
        """Ensure trace step IDs remain unique across repair attempts."""
        for idx, step in enumerate(trace_steps, start=1):
            base_id = step.step_id or f"step_{idx}"
            step.step_id = f"a{attempt_number}_{idx}_{base_id}"

    def _apply_prm_policy(
        self,
        *,
        task: Task,
        result: TaskResult,
        repair_attempts: int,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Apply optional PRM scoring + policy decision.

        Returns:
            (policy_action, reason, repair_hint)
        """
        prm_cfg_raw = task.metadata.get("prm")
        if not isinstance(prm_cfg_raw, dict) or not prm_cfg_raw.get("enabled", False):
            return None, None, None

        policy_config = PRMPolicyConfig(
            threshold=float(prm_cfg_raw.get("threshold", 0.72)),
            min_confidence=float(prm_cfg_raw.get("min_confidence", 0.65)),
            action_below_threshold=str(
                prm_cfg_raw.get("action_below_threshold", "retry_with_repair")
            ),  # type: ignore[arg-type]
            action_below_confidence=str(prm_cfg_raw.get("action_below_confidence", "block")),  # type: ignore[arg-type]
            max_repairs=int(prm_cfg_raw.get("max_repairs", 1)),
            strict_replay=bool(prm_cfg_raw.get("strict_replay", True)),
            enabled=bool(prm_cfg_raw.get("enabled", True)),
        )
        checkpoint = self._get_prm_checkpoint(result)
        current_snapshot = self._build_prm_replay_snapshot(task, prm_cfg_raw, result.prm_result)
        saved_snapshot = checkpoint.get("replay_snapshot")
        if not isinstance(saved_snapshot, dict) or not saved_snapshot:
            checkpoint["replay_snapshot"] = current_snapshot
        elif policy_config.strict_replay and saved_snapshot != current_snapshot:
            reason = "PRM replay incompatible: model/version/prompt hash changed."
            result.prm_result = PRMRunResult(
                passed=False,
                aggregate_score=0.0,
                aggregate_confidence=0.0,
                threshold=policy_config.threshold,
                scored_steps=[],
                policy_action="block",
                repair_hint=None,
                error=reason,
            )
            self._set_prm_checkpoint(result, checkpoint)
            return "block", reason, None

        scored_ids = {
            s.step_id
            for s in (result.prm_result.scored_steps if result.prm_result else [])
            if s.step_id
        }
        delta_steps = [s for s in result.trace_steps if s.step_id not in scored_ids]
        prm_error = None

        if self._prm_circuit_open:
            decision = evaluate_prm_policy(
                None,
                policy_config,
                repair_attempts_used=repair_attempts,
            )
            reason = f"PRM backend circuit open after {self._prm_backend_failures} failures"
            result.prm_result = PRMRunResult(
                passed=decision.passed,
                aggregate_score=0.0,
                aggregate_confidence=0.0,
                threshold=policy_config.threshold,
                scored_steps=[],
                policy_action=decision.action,
                repair_hint=None,
                error=reason,
            )
            self._set_prm_checkpoint(result, checkpoint)
            self._record_prm_event(
                "veridian.prm.policy_decision",
                {
                    "task.id": task.id,
                    "run.id": self._run_id,
                    "prm.model_id": current_snapshot.get("model_id", ""),
                    "prm.version": current_snapshot.get("version", ""),
                    "prm.aggregate_score": 0.0,
                    "prm.aggregate_confidence": 0.0,
                    "prm.policy_action": decision.action,
                    "prm.error": reason,
                },
            )
            return decision.action, reason, None

        if delta_steps:
            try:
                prm_start = time.perf_counter()
                invocation_id = self._build_prm_invocation_id(task.id, delta_steps)
                known_invocations = {
                    str(v)
                    for v in checkpoint.get("activity_invocation_ids", [])
                    if isinstance(v, str)
                }
                if invocation_id in known_invocations:
                    decision = evaluate_prm_policy(
                        result.prm_result,
                        policy_config,
                        repair_attempts_used=repair_attempts,
                    )
                    if result.prm_result is not None:
                        result.prm_result.policy_action = decision.action
                    self._set_prm_checkpoint(result, checkpoint)
                    return (
                        decision.action,
                        decision.reason,
                        (result.prm_result.repair_hint if result.prm_result else None),
                    )

                prm_budget_raw = prm_cfg_raw.get("budget", {})
                budget = (
                    PRMBudget.from_dict(prm_budget_raw)
                    if isinstance(prm_budget_raw, dict)
                    else PRMBudget()
                )
                if budget.max_steps_per_call and len(delta_steps) > budget.max_steps_per_call:
                    raise RuntimeError(
                        f"PRM budget exceeded: steps {len(delta_steps)} > {budget.max_steps_per_call}"
                    )
                estimated_tokens = self._estimate_prm_tokens(delta_steps)
                if budget.max_tokens_per_call and estimated_tokens > budget.max_tokens_per_call:
                    raise RuntimeError(
                        f"PRM budget exceeded: tokens {estimated_tokens} > {budget.max_tokens_per_call}"
                    )
                if budget.max_total_cost_usd:
                    estimated_cost = round(estimated_tokens * 0.000002, 6)
                    if estimated_cost > budget.max_total_cost_usd:
                        raise RuntimeError(
                            f"PRM budget exceeded: cost {estimated_cost} > {budget.max_total_cost_usd}"
                        )
                prm_verifier_id = str(prm_cfg_raw.get("verifier_id", "prm_reference"))
                prm_verifier_cfg = prm_cfg_raw.get("verifier_config", {})
                if not self._verifier_registry:
                    import veridian.verify.builtin  # noqa: F401, PLC0415
                    from veridian.verify.base import registry  # noqa: PLC0415

                    self._verifier_registry = registry
                prm_verifier = self._verifier_registry.get(
                    prm_verifier_id,
                    prm_verifier_cfg
                    if isinstance(prm_verifier_cfg, dict) and prm_verifier_cfg
                    else None,
                )
                scorer = getattr(prm_verifier, "score_steps", None)
                if scorer is None:
                    raise TypeError(
                        f"PRM verifier {prm_verifier_id!r} does not implement score_steps()"
                    )
                delta_result = scorer(
                    task_id=task.id,
                    steps=delta_steps,
                    context={"task_title": task.title, "repair_attempts": repair_attempts},
                    budget=budget,
                )
                previous_scores = result.prm_result.scored_steps if result.prm_result else []
                score_by_step: dict[str, PRMScore] = {s.step_id: s for s in previous_scores}
                for scored in delta_result.scored_steps:
                    score_by_step[scored.step_id] = scored
                ordered_scores = [
                    score_by_step[s.step_id]
                    for s in result.trace_steps
                    if s.step_id in score_by_step
                ]
                # Policy should evaluate the latest delta window so repair attempts can recover.
                aggregate_score = float(delta_result.aggregate_score)
                aggregate_confidence = float(delta_result.aggregate_confidence)
                threshold = float(prm_cfg_raw.get("threshold", delta_result.threshold))
                min_confidence = float(
                    prm_cfg_raw.get("min_confidence", policy_config.min_confidence)
                )
                result.prm_result = PRMRunResult(
                    passed=aggregate_score >= threshold and aggregate_confidence >= min_confidence,
                    aggregate_score=aggregate_score,
                    aggregate_confidence=aggregate_confidence,
                    threshold=threshold,
                    scored_steps=ordered_scores,
                    policy_action=delta_result.policy_action,
                    repair_hint=delta_result.repair_hint,
                    error=delta_result.error,
                )
                prm_ms = round((time.perf_counter() - prm_start) * 1000, 1)
                result.timing["prm_ms"] = prm_ms
                if budget.max_latency_ms and prm_ms > budget.max_latency_ms:
                    raise RuntimeError(
                        f"PRM budget exceeded: latency {prm_ms}ms > {budget.max_latency_ms}ms"
                    )
                known_invocations.add(invocation_id)
                checkpoint["activity_invocation_ids"] = sorted(known_invocations)
                checkpoint["prm_scored_until_step_id"] = (
                    ordered_scores[-1].step_id
                    if ordered_scores
                    else checkpoint.get("prm_scored_until_step_id")
                )
                history = checkpoint.get("prm_run_history", [])
                if not isinstance(history, list):
                    history = []
                history.append(result.prm_result.to_dict())
                checkpoint["prm_run_history"] = history[-50:]
                self._prm_backend_failures = 0
                self._prm_circuit_open = False
                self._record_prm_event(
                    "veridian.prm.score_steps",
                    {
                        "task.id": task.id,
                        "run.id": self._run_id,
                        "prm.model_id": current_snapshot.get("model_id", ""),
                        "prm.version": current_snapshot.get("version", ""),
                        "prm.aggregate_score": result.prm_result.aggregate_score,
                        "prm.aggregate_confidence": result.prm_result.aggregate_confidence,
                        "prm.policy_action": result.prm_result.policy_action,
                        "prm.latency_ms": result.timing.get("prm_ms", 0.0),
                        "prm.step_count": len(delta_steps),
                    },
                )
            except Exception as exc:
                prm_error = str(exc)[:300]
                self._prm_backend_failures += 1
                if self._prm_backend_failures >= self._prm_circuit_threshold:
                    self._prm_circuit_open = True
                log.warning("runner.prm_error task_id=%s err=%s", task.id, prm_error)
                self._record_prm_event(
                    "veridian.prm.score_steps",
                    {
                        "task.id": task.id,
                        "run.id": self._run_id,
                        "prm.model_id": current_snapshot.get("model_id", ""),
                        "prm.version": current_snapshot.get("version", ""),
                        "prm.aggregate_score": 0.0,
                        "prm.aggregate_confidence": 0.0,
                        "prm.policy_action": "error",
                        "prm.latency_ms": result.timing.get("prm_ms", 0.0),
                        "prm.error": prm_error,
                        "prm.circuit_open": self._prm_circuit_open,
                    },
                )

        decision = evaluate_prm_policy(
            result.prm_result,
            policy_config,
            repair_attempts_used=repair_attempts,
        )
        if result.prm_result is None:
            result.prm_result = PRMRunResult(
                passed=decision.passed,
                aggregate_score=0.0,
                aggregate_confidence=0.0,
                threshold=policy_config.threshold,
                scored_steps=[],
                policy_action=decision.action,
                repair_hint=None,
                error=prm_error or decision.reason,
            )
        else:
            result.prm_result.policy_action = decision.action
            if decision.action == "retry_with_repair" and not result.prm_result.repair_hint:
                result.prm_result.repair_hint = decision.reason

        self._record_prm_event(
            "veridian.prm.policy_decision",
            {
                "task.id": task.id,
                "run.id": self._run_id,
                "prm.model_id": current_snapshot.get("model_id", ""),
                "prm.version": current_snapshot.get("version", ""),
                "prm.aggregate_score": float(result.prm_result.aggregate_score)
                if result.prm_result
                else 0.0,
                "prm.aggregate_confidence": float(result.prm_result.aggregate_confidence)
                if result.prm_result
                else 0.0,
                "prm.policy_action": decision.action,
                "prm.latency_ms": result.timing.get("prm_ms", 0.0),
            },
        )
        self._set_prm_checkpoint(result, checkpoint)
        return decision.action, decision.reason, result.prm_result.repair_hint

    def _get_prm_checkpoint(self, result: TaskResult | None) -> dict[str, Any]:
        """Return mutable PRM checkpoint state with required defaults."""
        defaults: dict[str, Any] = {
            "prm_scored_until_step_id": None,
            "policy_action_log": [],
            "prm_run_history": [],
            "activity_invocation_ids": [],
            "repair_attempts": 0,
            "ready_to_finalize": False,
            "last_verification_passed": False,
            "last_error": "",
            "last_policy_action": "",
            "replay_snapshot": {},
        }
        if result is None:
            return dict(defaults)
        raw = result.extras.get("prm_checkpoint")
        checkpoint = raw if isinstance(raw, dict) else {}
        for key, value in defaults.items():
            checkpoint.setdefault(key, value if not isinstance(value, list) else list(value))
        result.extras["prm_checkpoint"] = checkpoint
        return checkpoint

    def _set_prm_checkpoint(self, result: TaskResult, checkpoint: dict[str, Any]) -> None:
        """Persist checkpoint state into TaskResult extras."""
        result.extras["prm_checkpoint"] = checkpoint

    def _record_prm_policy_checkpoint(
        self,
        *,
        result: TaskResult,
        action: str | None,
        reason: str | None,
        repair_attempts: int,
    ) -> None:
        if not action:
            return
        checkpoint = self._get_prm_checkpoint(result)
        history = checkpoint.get("policy_action_log", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "action": action,
                "reason": reason or "",
                "repair_attempt": repair_attempts,
                "timestamp_ms": int(time.time() * 1000),
            }
        )
        checkpoint["policy_action_log"] = history[-200:]
        checkpoint["last_policy_action"] = action
        self._set_prm_checkpoint(result, checkpoint)

    def _set_prm_checkpoint_outcome(
        self,
        *,
        result: TaskResult,
        verification_passed: bool,
        error: str,
        policy_action: str | None,
        ready_to_finalize: bool,
        repair_attempts: int,
    ) -> None:
        checkpoint = self._get_prm_checkpoint(result)
        checkpoint["last_verification_passed"] = bool(verification_passed)
        checkpoint["last_error"] = (error or "")[:300]
        checkpoint["last_policy_action"] = policy_action or checkpoint.get("last_policy_action", "")
        checkpoint["ready_to_finalize"] = bool(ready_to_finalize)
        checkpoint["repair_attempts"] = int(repair_attempts)
        self._set_prm_checkpoint(result, checkpoint)

    def _persist_prm_checkpoint(
        self,
        task_id: str,
        result: TaskResult,
        *,
        repair_attempts: int,
    ) -> None:
        """Durably persist intermediate PRM state without changing task status."""
        checkpoint = result.extras.get("prm_checkpoint")
        if not isinstance(checkpoint, dict):
            return
        checkpoint["repair_attempts"] = int(repair_attempts)
        result.extras["prm_checkpoint"] = checkpoint
        try:
            self.ledger.checkpoint_result(task_id, result)
        except Exception as exc:
            log.debug("runner.prm_checkpoint_persist_failed task_id=%s err=%s", task_id, exc)

    def _build_prm_replay_snapshot(
        self,
        task: Task,
        prm_cfg: dict[str, Any],
        prm_result: PRMRunResult | None,
    ) -> dict[str, str]:
        """Build deterministic replay compatibility snapshot."""
        model_id = str(prm_cfg.get("verifier_id", "prm_reference"))
        version = "1"
        if isinstance(prm_cfg.get("verifier_config"), dict):
            version = str(prm_cfg["verifier_config"].get("version", version))
        if prm_result and prm_result.scored_steps:
            first = prm_result.scored_steps[0]
            model_id = first.model_id or model_id
            version = first.version or version

        prompt_material = {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "verifier_id": prm_cfg.get("verifier_id", "prm_reference"),
            "verifier_config": prm_cfg.get("verifier_config", {}),
            "threshold": prm_cfg.get("threshold", 0.72),
            "min_confidence": prm_cfg.get("min_confidence", 0.65),
        }
        prompt_hash = hashlib.sha256(
            json.dumps(prompt_material, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return {"model_id": model_id, "version": version, "prompt_hash": prompt_hash}

    def _check_prm_replay_compatibility(
        self,
        task: Task,
        result: TaskResult | None,
    ) -> str | None:
        """Return fail-closed error when strict replay snapshot mismatches."""
        if result is None:
            return None
        prm_cfg_raw = task.metadata.get("prm")
        if not isinstance(prm_cfg_raw, dict) or not prm_cfg_raw.get("enabled", False):
            return None
        if not bool(prm_cfg_raw.get("strict_replay", True)):
            return None

        checkpoint = self._get_prm_checkpoint(result)
        saved_snapshot = checkpoint.get("replay_snapshot")
        if not isinstance(saved_snapshot, dict) or not saved_snapshot:
            return None
        current_snapshot = self._build_prm_replay_snapshot(task, prm_cfg_raw, result.prm_result)
        if saved_snapshot != current_snapshot:
            return (
                "PRM replay incompatible: model/version/prompt hash changed; "
                "strict replay requires blocking."
            )[:300]
        return None

    def _build_prm_invocation_id(self, task_id: str, steps: list[TraceStep]) -> str:
        if not steps:
            return f"{task_id}:empty"
        payload = {
            "task_id": task_id,
            "first_step_id": steps[0].step_id,
            "last_step_id": steps[-1].step_id,
            "count": len(steps),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"prm:{digest}"

    def _estimate_prm_tokens(self, steps: list[TraceStep]) -> int:
        """Best-effort token estimate for PRM budget guards."""
        total = 0
        for step in steps:
            if isinstance(step.token_count, int) and step.token_count > 0:
                total += step.token_count
                continue
            # Rough fallback for when provider token usage is unavailable.
            total += max(1, len(step.content.split()))
        return total

    def _record_prm_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        """Emit PRM telemetry if tracing is enabled."""
        if self._tracer is None:
            return
        with contextlib.suppress(Exception):
            self._tracer.record_event(event_type, attributes)

    def _setup_signal_handler(self) -> None:
        """Register SIGINT handler to set shutdown flag (no mid-task exit)."""

        def _handler(signum: int, frame: object) -> None:
            log.warning("runner.sigint_received — will stop after current task")
            self._shutdown = True

        with contextlib.suppress(OSError, ValueError):
            # signal.signal fails in non-main threads — ignore
            signal.signal(signal.SIGINT, _handler)
