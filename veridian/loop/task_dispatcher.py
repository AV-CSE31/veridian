"""
veridian.loop.task_dispatcher
---------------------------------------------------------------
Per-task processing extracted from VeridianRunner.

Owns the inner task loop:
  - claim/dispatch/verify cycle
  - pause/resume routing on ControlFlowSignal
  - verifier dispatch and confidence envelope building
  - trace-step namespacing

The dispatcher is package-private. VeridianRunner exposes thin wrappers
(``_task_loop``, ``_verifier_registry``) so existing tests that mock
those attributes keep working.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from veridian.context.manager import ContextManager
from veridian.core.config import VeridianConfig
from veridian.core.events import (
    TaskClaimed,
    TaskCompleted,
    TaskFailed,
    TaskPaused,
    TaskResumed,
)
from veridian.core.exceptions import (
    ControlFlowSignal,
    HumanReviewRequired,
    RunAbortRequested,
    TaskPauseRequested,
)
from veridian.core.task import Task, TaskResult, TaskStatus, TraceStep
from veridian.hooks.registry import HookRegistry
from veridian.loop.replay_compat import build_run_replay_snapshot, check_replay_compatibility
from veridian.loop.runtime_store import RuntimeStore
from veridian.loop.worker import WorkerAgent
from veridian.providers.base import LLMProvider
from veridian.verify.base import VerifierRegistry

if TYPE_CHECKING:
    from veridian.loop.run_controller import _RunController
    from veridian.loop.runner import RunSummary

__all__ = ["_TaskDispatcher"]

log = logging.getLogger(__name__)


class _TaskDispatcher:
    """The inner task loop. Package-private; owned by VeridianRunner."""

    def __init__(
        self,
        ledger: RuntimeStore,
        provider: LLMProvider,
        config: VeridianConfig,
        hooks: HookRegistry,
        context_manager: ContextManager,
        verifier_registry: VerifierRegistry,
        controller: _RunController,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.config = config
        self.hooks = hooks
        self.context_manager = context_manager
        self.verifier_registry = verifier_registry
        self.controller = controller

    # ------ main loop --------------------------------------------------------
    def run_loop(self, run_id: str, phase: str | None, summary: RunSummary) -> None:
        """Process tasks until the queue is empty or shutdown is signalled."""
        include_paused = bool(getattr(self.config, "resume_paused_on_start", True))
        paused_this_run: set[str] = set()
        while not self.controller.shutdown:
            task = self.ledger.get_next(phase=phase, include_paused=include_paused)
            if task is None:
                break

            if task.id in paused_this_run:
                other_paused = self.ledger.list(status=TaskStatus.PAUSED)
                if phase:
                    other_paused = [t for t in other_paused if t.phase == phase]
                next_paused = next((t for t in other_paused if t.id not in paused_this_run), None)
                if next_paused is not None:
                    task = next_paused
                else:
                    task = self.ledger.get_next(phase=phase, include_paused=False)
                    if task is None:
                        break

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
                except RunAbortRequested as signal:
                    self._handle_run_abort(signal, summary)
                    return
                except ControlFlowSignal as signal:
                    self._handle_pause_signal(task, run_id, signal, summary)
                    paused_this_run.add(task.id)
                    continue

            try:
                self._process_task(task, run_id, summary)
            except RunAbortRequested as signal:
                self._handle_run_abort(signal, summary)
                return
            except ControlFlowSignal as signal:
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

    # ------ run abort routing -----------------------------------------------
    def _handle_run_abort(self, signal: RunAbortRequested, summary: RunSummary) -> None:
        """Halt the run: request shutdown so future iterations break."""
        self.controller.request_shutdown()
        msg = f"run_aborted: {signal.reason}"
        if signal.source:
            msg = f"run_aborted[{signal.source}]: {signal.reason}"
        summary.errors.append(msg)
        log.warning("runner.run_aborted source=%s reason=%s", signal.source, signal.reason)

    # ------ pause routing ----------------------------------------------------
    def _handle_pause_signal(
        self,
        task: Task,
        run_id: str,
        signal: ControlFlowSignal,
        summary: RunSummary,
    ) -> None:
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
            TaskPaused(run_id=run_id, task=paused_task, reason=reason, payload=payload),
        )
        log.info("runner.task_paused task_id=%s reason=%s", task.id, reason[:80])

    # ------ per-task processing ---------------------------------------------
    def _process_task(self, task: Task, run_id: str, summary: RunSummary) -> None:
        task = self.ledger.claim(task.id, run_id)
        self.hooks.fire("before_task", TaskClaimed(run_id=run_id, task=task))

        if self.config.dry_run:
            log.info("runner.dry_run task_id=%s title=%s", task.id, task.title[:60])
            self.ledger.skip(task.id, reason="dry_run")
            return

        worker = WorkerAgent(
            provider=self.provider,
            config=self.config,
            context_manager=self.context_manager,
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
        grader_meta = verify_meta.get("grader_metadata")
        if isinstance(grader_meta, dict):
            result.extras["grader_metadata"] = grader_meta
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

    # ------ verifier dispatch + confidence ----------------------------------
    def _verify(self, task: Task, result: TaskResult) -> tuple[bool, str, dict[str, Any]]:
        verify_start = time.perf_counter()
        if not self.verifier_registry:
            try:
                from veridian.verify.base import registry  # noqa: PLC0415

                self.verifier_registry = registry
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
            verifier = self.verifier_registry.get(task.verifier_id, config or None)
            vresult = verifier.verify(task, result)
            return (
                vresult.passed,
                vresult.error or "",
                {
                    "score": vresult.score,
                    "evidence": vresult.evidence or {},
                    "verification_ms": round((time.perf_counter() - verify_start) * 1000, 1),
                    "grader_metadata": self._grader_metadata(task, verifier),
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

    def _grader_metadata(self, task: Task, verifier: Any) -> dict[str, Any]:
        """Pin verifier identity + config digest into the audit trail.

        Lets operators detect rubric drift and pinpoint which verifier
        version produced a given verdict. The provider's model id is
        included opportunistically — useful when an operator routes the
        verifier through an LLM-backed provider.
        """
        cls = type(verifier)
        config = task.verifier_config or {}
        try:
            config_blob = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
            config_hash = hashlib.sha256(config_blob).hexdigest()[:16]
        except (TypeError, ValueError):
            config_hash = "__unhashable__"

        meta: dict[str, Any] = {
            "verifier_id": task.verifier_id,
            "verifier_class": f"{cls.__module__}:{cls.__qualname__}",
            "verifier_config_hash": config_hash,
        }
        provider_model = getattr(self.provider, "model", None)
        if isinstance(provider_model, str) and provider_model:
            meta["grader_provider_model"] = provider_model
        meta["grader_provider_class"] = type(self.provider).__name__
        return meta

    def _build_confidence(
        self,
        task: Task,
        verify_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
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
        for idx, step in enumerate(trace_steps, start=1):
            base_id = step.step_id or f"step_{idx}"
            step.step_id = f"a{attempt_number}_{idx}_{base_id}"
