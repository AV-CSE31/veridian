"""
veridian.core.exceptions

Core exceptions for the slim runtime.
"""


class VeridianError(Exception):
    """Base class for all Veridian errors."""


class VeridianConfigError(VeridianError):
    """Invalid configuration for a Veridian component."""


class InvalidTransition(VeridianError):
    """Attempted illegal task status transition."""


class LedgerCorrupted(VeridianError):
    """ledger.json could not be parsed or failed schema validation."""


class TaskNotFound(VeridianError):
    """Task ID not found in ledger."""


class TaskAlreadyClaimed(VeridianError):
    """Task is IN_PROGRESS and claimed by a different runner."""


class VerificationError(VeridianError):
    """Verifier raised an internal exception."""


class VerifierNotFound(VeridianError):
    """verifier_id not registered in VerifierRegistry."""


class ProviderError(VeridianError):
    """LLM API call failed after all retries."""


class ProviderRateLimited(ProviderError):
    """Rate limit hit and circuit breaker opened."""


class ContextWindowExceeded(ProviderError):
    """Prompt exceeds provider context limit."""


class ExecutorError(VeridianError):
    """Bash command could not be executed."""


class BlockedCommand(ExecutorError):
    """Command matched the bash blocklist and was refused."""


class ExecutorTimeout(ExecutorError):
    """Command exceeded task_timeout_seconds."""


class ControlFlowSignal(VeridianError):
    """Base class for hook-raised control-flow signals."""


class RunAbortRequested(ControlFlowSignal):
    """Hook-raised signal that halts the entire run.

    Distinct from :class:`TaskPauseRequested` (which suspends one task and
    leaves the queue running). The dispatcher catches this in the outer
    loop, records the reason in ``RunSummary.errors``, and breaks out so
    no further tasks are claimed.
    """

    def __init__(self, reason: str, source: str = "") -> None:
        self.reason = reason
        self.source = source
        msg = f"Run aborted: {reason}"
        if source:
            msg = f"Run aborted by {source}: {reason}"
        super().__init__(msg)


class CostLimitExceeded(RunAbortRequested):
    """CostGuardHook: cumulative cost exceeded max_cost_usd.

    Routed through RunAbortRequested so the dispatcher halts the run
    instead of swallowing or pausing a single task.
    """

    def __init__(self, current: float, limit: float):
        self.current = current
        self.limit = limit
        super().__init__(
            reason=f"cost ${current:.4f} exceeded limit ${limit:.2f}",
            source="cost_guard",
        )


class WallClockBudgetExceeded(RunAbortRequested):
    """WallClockBudgetHook: run wall-clock duration exceeded max_seconds."""

    def __init__(self, elapsed: float, limit: float):
        self.elapsed = elapsed
        self.limit = limit
        super().__init__(
            reason=f"wall clock {elapsed:.1f}s exceeded budget {limit:.1f}s",
            source="wall_clock_budget",
        )


class RepetitionDetected(RunAbortRequested):
    """RepetitionGuardHook: N consecutive task outputs hashed identically."""

    def __init__(self, window: int, fingerprint: str):
        self.window = window
        self.fingerprint = fingerprint
        super().__init__(
            reason=f"{window} consecutive identical outputs (fp={fingerprint[:8]})",
            source="repetition_guard",
        )


class HumanReviewRequired(ControlFlowSignal):
    """HumanReviewHook: task requires human approval before proceeding."""

    def __init__(self, task_id: str, reason: str):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Human review required for task {task_id}: {reason}")


class TaskPauseRequested(ControlFlowSignal):
    """Generic pause signal raised by hooks that need to suspend a task."""

    def __init__(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.task_id = task_id
        self.reason = reason
        self.payload: dict[str, object] = payload or {}
        super().__init__(f"Task {task_id} paused: {reason}")


class TaskNotPaused(VeridianError):
    """Ledger.resume() was called on a task whose status is not PAUSED."""

    def __init__(self, task_id: str, status: str) -> None:
        self.task_id = task_id
        self.status = status
        super().__init__(f"Task {task_id} is not paused (status={status})")
