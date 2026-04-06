"""
veridian.integrations.sdk
──────────────────────────
RV3-009: Stable integration SDK.

Small, versioned facade exposing the six primitives that third-party adapters
(LangGraph, CrewAI, AutoGen, custom) should import from a single stable
namespace:

  - start_run(config)          → RunContext
  - record_step(ctx, step)     → None
  - verify_output(ctx, ...)    → VerificationOutcome
  - persist_state(ctx, ...)    → None
  - resume_run(ctx, task_id)   → RunContext
  - replay_run(ctx, task_id)   → ReplayReport

The SDK is intentionally narrow — it wraps VeridianRunner primitives without
exposing the full internal class hierarchy. All adapters depend on this module
and only this module; it is the only surface semver-scoped for v3.

API stability tier: **STABLE** as of v3. Backward-incompatible changes require
a major version bump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult, TraceStep
from veridian.ledger.ledger import TaskLedger
from veridian.loop.activity import ActivityJournal
from veridian.loop.replay_compat import (
    build_run_replay_snapshot,
    check_replay_compatibility,
)
from veridian.loop.runtime_store import RuntimeStore
from veridian.providers.base import LLMProvider
from veridian.verify.base import VerifierRegistry
from veridian.verify.base import registry as default_verifier_registry

__all__ = [
    "SDK_VERSION",
    "RunContext",
    "VerificationOutcome",
    "ReplayReport",
    "start_run",
    "record_step",
    "verify_output",
    "persist_state",
    "resume_run",
    "replay_run",
]

# Major.minor — stability tier for the SDK surface. Bump major on breaking
# change; bump minor on additive change.
SDK_VERSION: str = "1.0"


@dataclass
class RunContext:
    """Handle returned by ``start_run``. Opaque to adapters — all operations
    go through the SDK functions rather than direct attribute access.

    Attributes are kept public for debugging but should be treated as internal.
    """

    run_id: str
    config: VeridianConfig
    ledger: RuntimeStore
    provider: LLMProvider
    verifier_registry: VerifierRegistry
    activity_journal: ActivityJournal = field(default_factory=ActivityJournal)
    trace_steps: list[TraceStep] = field(default_factory=list)
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationOutcome:
    """Outcome of ``verify_output``. Framework-agnostic shape."""

    passed: bool
    error: str | None
    evidence: dict[str, Any]
    score: float | None
    verifier_id: str


@dataclass
class ReplayReport:
    """Summary returned by ``replay_run``."""

    task_id: str
    activity_journal_entries: int
    snapshot: dict[str, str]
    replay_incompatible_reason: str | None
    status: str


def start_run(
    *,
    config: VeridianConfig,
    provider: LLMProvider,
    ledger: RuntimeStore | None = None,
    run_id: str | None = None,
    verifier_registry: VerifierRegistry | None = None,
) -> RunContext:
    """Begin a new Veridian-managed run.

    Adapters call this at the start of a framework-specific session. The
    returned ``RunContext`` is passed to every subsequent SDK call. Opens a
    fresh activity journal; caller is responsible for persisting it via
    ``persist_state``.
    """
    import uuid

    if ledger is None:
        ledger_path = (
            config.ledger_file
            if isinstance(config.ledger_file, Path)
            else Path(str(config.ledger_file))
        )
        ledger = TaskLedger(path=ledger_path, progress_file=str(config.progress_file))

    if verifier_registry is None:
        import veridian.verify.builtin  # noqa: F401 — trigger registration

        verifier_registry = default_verifier_registry

    return RunContext(
        run_id=run_id or str(uuid.uuid4())[:8],
        config=config,
        ledger=ledger,
        provider=provider,
        verifier_registry=verifier_registry,
    )


def record_step(ctx: RunContext, step: TraceStep) -> None:
    """Append a trace step to the context. Adapters call this after each
    framework-level node/edge transition so PRM scoring and replay diffs see
    a complete trace.
    """
    ctx.trace_steps.append(step)


def verify_output(
    ctx: RunContext,
    *,
    task: Task,
    output: Any,
    verifier_id: str | None = None,
    verifier_config: dict[str, Any] | None = None,
) -> VerificationOutcome:
    """Run a verifier against an output and return a framework-agnostic outcome.

    ``task`` provides the verification contract (verifier_id, verifier_config).
    ``output`` is the framework's output object — the SDK wraps it in a minimal
    TaskResult. The caller may override the verifier via ``verifier_id``.
    """
    vid = verifier_id or task.verifier_id
    vcfg = verifier_config if verifier_config is not None else task.verifier_config
    verifier = ctx.verifier_registry.get(vid, vcfg or None)

    # Wrap output in a TaskResult shape the existing verifiers understand.
    if isinstance(output, TaskResult):
        result = output
    elif isinstance(output, dict):
        result = TaskResult(raw_output=str(output), structured=output)
    else:
        result = TaskResult(raw_output=str(output))

    vres = verifier.verify(task, result)
    return VerificationOutcome(
        passed=vres.passed,
        error=vres.error,
        evidence=vres.evidence or {},
        score=vres.score,
        verifier_id=vid,
    )


def persist_state(
    ctx: RunContext,
    *,
    task_id: str,
    result: TaskResult | None = None,
    include_journal: bool = True,
    include_replay_snapshot: bool = True,
) -> None:
    """Checkpoint the current run state to the ledger.

    Persists the trace steps, activity journal, and replay snapshot under
    the given task so a subsequent ``resume_run`` / ``replay_run`` returns the
    same state. Uses ``TaskLedger.checkpoint_result`` — never changes task
    lifecycle status.
    """
    if result is None:
        # Load existing or create a fresh TaskResult so extras can attach.
        existing = ctx.ledger.get(task_id)
        result = existing.result or TaskResult(raw_output="")

    # Merge trace steps (de-dup by step_id)
    existing_ids = {s.step_id for s in result.trace_steps}
    for step in ctx.trace_steps:
        if step.step_id not in existing_ids:
            result.trace_steps.append(step)

    if include_journal:
        result.extras["activity_journal"] = ctx.activity_journal.to_list()

    if include_replay_snapshot:
        try:
            task = ctx.ledger.get(task_id)
            snap = build_run_replay_snapshot(task, ctx.provider)
            result.extras["run_replay_snapshot"] = snap.to_dict()
        except Exception:
            # Snapshot is best-effort; don't block persistence on failure.
            pass

    ctx.ledger.checkpoint_result(task_id, result)
    ctx.task_id = task_id


def resume_run(
    *,
    config: VeridianConfig,
    provider: LLMProvider,
    task_id: str,
    run_id: str | None = None,
    ledger: RuntimeStore | None = None,
) -> RunContext:
    """Restore a RunContext from a previously persisted task.

    Reads the task's result.extras, rehydrates the activity journal + trace
    steps, and returns a new RunContext ready for further ``record_step`` /
    ``persist_state`` calls. If the task is in PAUSED state the adapter is
    responsible for calling ``ledger.resume(task_id, run_id)`` when it wants
    to transition back to IN_PROGRESS.
    """
    ctx = start_run(config=config, provider=provider, ledger=ledger, run_id=run_id)
    task = ctx.ledger.get(task_id)
    ctx.task_id = task_id
    if task.result is not None:
        ctx.trace_steps = list(task.result.trace_steps)
        journal_data = task.result.extras.get("activity_journal")
        if isinstance(journal_data, list):
            ctx.activity_journal = ActivityJournal.from_list(journal_data)
    return ctx


def replay_run(ctx: RunContext, task_id: str) -> ReplayReport:
    """Build a replay report for a task: journal stats, current snapshot,
    and any replay_incompatible reason (under strict mode).

    Adapters use this to answer "what would happen if I re-run this task?".
    Does NOT execute anything — read-only.
    """
    task = ctx.ledger.get(task_id)
    result = task.result
    journal_count = 0
    snapshot_dict: dict[str, str] = {}
    incompat_reason: str | None = None

    current_snap = build_run_replay_snapshot(task, ctx.provider)
    snapshot_dict = current_snap.to_dict()

    if result is not None:
        journal_data = result.extras.get("activity_journal", [])
        if isinstance(journal_data, list):
            journal_count = len(journal_data)

        saved_snap = result.extras.get("run_replay_snapshot")
        if isinstance(saved_snap, dict):
            incompat_reason = check_replay_compatibility(
                task=task,
                current=current_snap,
                saved=saved_snap,
                strict=bool(getattr(ctx.config, "strict_replay", False)),
            )

    return ReplayReport(
        task_id=task_id,
        activity_journal_entries=journal_count,
        snapshot=snapshot_dict,
        replay_incompatible_reason=incompat_reason,
        status=task.status.value,
    )
