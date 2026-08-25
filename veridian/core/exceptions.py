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


class HardControlViolation(VeridianError):
    """A mandatory safety or authorization control denied an operation."""


class CostLimitExceeded(HardControlViolation):
    """CostGuardHook: cumulative cost exceeded max_cost_usd."""

    def __init__(self, current: float, limit: float):
        self.current = current
        self.limit = limit
        super().__init__(f"Cost ${current:.4f} exceeded limit ${limit:.2f}")


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
