"""
veridian.core.exceptions
───────────────────────
All veridian exceptions. Import from here, never from submodules.
"""


class VeridianError(Exception):
    """Base class for all veridian errors."""


class VeridianConfigError(VeridianError):
    """Invalid configuration for a Veridian component."""


# ── Ledger ────────────────────────────────────────────────────────────────────

class InvalidTransition(VeridianError):
    """Attempted illegal task status transition."""


class LedgerCorrupted(VeridianError):
    """ledger.json could not be parsed or failed schema validation."""


class TaskNotFound(VeridianError):
    """Task ID not found in ledger."""


class TaskAlreadyClaimed(VeridianError):
    """Task is IN_PROGRESS and claimed by a different runner."""


# ── Verification ──────────────────────────────────────────────────────────────

class VerificationError(VeridianError):
    """Verifier raised an internal exception (not a failing verification result)."""


class VerifierNotFound(VeridianError):
    """verifier_id not registered in VerifierRegistry."""


# ── Provider / LLM ────────────────────────────────────────────────────────────

class ProviderError(VeridianError):
    """LLM API call failed after all retries."""


class ProviderRateLimited(ProviderError):
    """Rate limit hit and circuit breaker opened."""


class ContextWindowExceeded(ProviderError):
    """Prompt exceeds provider context limit."""


# ── Executor ──────────────────────────────────────────────────────────────────

class ExecutorError(VeridianError):
    """Bash command could not be executed (not the same as exit code != 0)."""


class BlockedCommand(ExecutorError):
    """Command matched the bash_blocklist and was refused."""


class ExecutorTimeout(ExecutorError):
    """Command exceeded task_timeout_seconds."""


# ── Flow control (raised by hooks to modify runner behaviour) ─────────────────

class CostLimitExceeded(VeridianError):
    """CostGuardHook: cumulative cost exceeded max_cost_usd. Run halts."""
    def __init__(self, current: float, limit: float):
        self.current = current
        self.limit = limit
        super().__init__(f"Cost ${current:.4f} exceeded limit ${limit:.2f}")


class HumanReviewRequired(VeridianError):
    """HumanReviewHook: task requires human approval before proceeding."""
    def __init__(self, task_id: str, reason: str):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Human review required for task {task_id}: {reason}")


class DriftDetected(VeridianError):
    """DriftDetectorHook: agent behavior has drifted beyond threshold."""
    def __init__(self, metric: str, magnitude: float, direction: str):
        self.metric = metric
        self.magnitude = magnitude
        self.direction = direction
        super().__init__(
            f"Drift detected in '{metric}': {direction} by {magnitude:.2%}"
        )


class RunAborted(VeridianError):
    """Runner was externally aborted (e.g. SIGINT, dry_run assertion)."""


# ── Storage ────────────────────────────────────────────────────────────────────

class StorageError(VeridianError):
    """A storage backend operation failed."""


class StorageLockError(StorageError):
    """Could not acquire distributed lock on the storage backend."""


class StorageConnectionError(StorageError):
    """Could not connect to the storage backend."""


# ── Entropy / GC ──────────────────────────────────────────────────────────────

class EntropyError(VeridianError):
    """EntropyGC encountered an unexpected error during a consistency check."""


# ── Observability ─────────────────────────────────────────────────────────────

class TracerError(VeridianError):
    """VeridianTracer encountered an error initialising or emitting a trace event."""
