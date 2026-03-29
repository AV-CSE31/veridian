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
        super().__init__(f"Drift detected in '{metric}': {direction} by {magnitude:.2%}")


class RunAborted(VeridianError):
    """Runner was externally aborted (e.g. SIGINT, dry_run assertion)."""


# ── Adversarial Evaluation ─────────────────────────────────────────────────────


class EvaluationError(VeridianError):
    """AdversarialEvaluator raised an internal exception during evaluation."""


class ContractViolation(VeridianError):
    """SprintContract validation failed — contract is invalid, unsigned, or criteria unmet.

    Supports two calling conventions:
      ContractViolation("plain message")               — contracts module style
      ContractViolation(contract_id=..., reason=...)   — eval pipeline style
    """

    def __init__(self, message: str = "", *, contract_id: str = "", reason: str = ""):
        self.contract_id = contract_id
        self.reason = reason or message
        if contract_id and reason:
            super().__init__(f"Contract '{contract_id}' violated: {reason}")
        else:
            super().__init__(message or reason)


class CalibrationError(VeridianError):
    """CalibrationProfile is invalid (e.g. rubric weights don't sum to 1.0)."""


class ContractNotFound(VeridianError):
    """Contract ID not found in ContractRegistry."""


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


# ── Tool Safety ──────────────────────────────────────────────────────────────


class ToolSafetyViolation(VeridianError):
    """Agent-generated code failed static safety analysis."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        summary = "; ".join(violations[:3])
        if len(violations) > 3:
            summary += f" (+{len(violations) - 3} more)"
        super().__init__(f"Tool safety violations: {summary}")


# ── Memory Integrity ─────────────────────────────────────────────────────────


class MemoryIntegrityViolation(VeridianError):
    """Memory update failed integrity checks (contradiction, bias, tampering)."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        summary = "; ".join(violations[:3])
        if len(violations) > 3:
            summary += f" (+{len(violations) - 3} more)"
        super().__init__(f"Memory integrity violations: {summary}")


# ── Verifier Integrity ───────────────────────────────────────────────────────


class VerifierIntegrityError(VeridianError):
    """Verifier chain was tampered with during a run."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Verifier integrity compromised: {detail}")


# ── Budget ─────────────────────────────────────────────────────────────────────


class BudgetExceeded(VeridianError):
    """A Budget limit (tokens, cost, or wall-clock) was exceeded.

    Attributes
    ----------
    limit_type : str
        One of ``"tokens"``, ``"cost_usd"``, or ``"wall_clock_seconds"``.
    current : float
        The current value that triggered the limit.
    limit : float
        The configured limit that was exceeded.
    """

    def __init__(self, limit_type: str, current: float, limit: float) -> None:
        self.limit_type = limit_type
        self.current = current
        self.limit = limit
        super().__init__(f"Budget exceeded: {limit_type} current={current:.4g} > limit={limit:.4g}")


# ── Knowledge Graph ────────────────────────────────────────────────────────────


class KnowledgeGraphError(VeridianError):
    """Knowledge graph operation failed."""


# ── Saga / Distributed Transactions ───────────────────────────────────────────


class SagaError(VeridianError):
    """Saga orchestration or step execution failed."""


class SagaRollbackError(SagaError):
    """One or more compensating transactions failed during saga rollback."""

    def __init__(self, failed_compensations: list[str]) -> None:
        self.failed_compensations = failed_compensations
        super().__init__(
            f"Saga rollback failed for steps: {', '.join(failed_compensations)}"
        )


# ── Checkpoint ─────────────────────────────────────────────────────────────────


class CheckpointError(VeridianError):
    """Checkpoint save or restore operation failed."""


# ── Pipeline ───────────────────────────────────────────────────────────────────


class PipelineError(VeridianError):
    """Verification pipeline configuration or execution error."""


# ── Consensus ──────────────────────────────────────────────────────────────────


class ConsensusError(VeridianError):
    """Multi-model consensus verification error."""


# ── Self-Improving ─────────────────────────────────────────────────────────────


class SelfImprovingError(VeridianError):
    """Self-improving verifier framework encountered an error."""


# ── Identity / PKI ────────────────────────────────────────────────────────────


class PKIError(VeridianError):
    """Agent identity or cryptographic operation failed."""


class SignatureVerificationError(PKIError):
    """Ed25519 signature verification failed — message may be tampered."""

    def __init__(self, agent_id: str, reason: str = "") -> None:
        self.agent_id = agent_id
        msg = f"Signature verification failed for agent '{agent_id}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class KeyRotationError(PKIError):
    """Key rotation operation failed (e.g. agent not found or already rotated)."""


class AgentIdentityNotFound(PKIError):
    """Agent ID not found in the identity registry."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Agent identity not found: '{agent_id}'")


# ── Natural Language Policy ────────────────────────────────────────────────────


class NLPolicyError(VeridianError):
    """Natural language policy interface encountered an error."""


class PolicyActivationRequired(NLPolicyError):
    """Policy draft must be reviewed and activated before use."""

    def __init__(self, draft_id: str) -> None:
        self.draft_id = draft_id
        super().__init__(f"Policy draft '{draft_id}' requires human review before activation.")


class PolicyNotFound(NLPolicyError):
    """Policy draft or active policy not found."""

    def __init__(self, policy_id: str) -> None:
        self.policy_id = policy_id
        super().__init__(f"Policy not found: '{policy_id}'")


# ── Explanation Engine ─────────────────────────────────────────────────────────


class ExplanationError(VeridianError):
    """Verification explanation engine encountered an error."""


# ── EU AI Act Compliance ───────────────────────────────────────────────────────


class ComplianceError(VeridianError):
    """EU AI Act compliance checker encountered an error."""


class ComplianceGapError(ComplianceError):
    """Required compliance articles are not covered by active verifiers."""

    def __init__(self, uncovered: list[str]) -> None:
        self.uncovered = uncovered
        articles = ", ".join(uncovered)
        super().__init__(f"Compliance gaps detected — uncovered articles: {articles}")


# ── Audit ─────────────────────────────────────────────────────────────────────


class AuditIntegrityError(VeridianError):
    """Cryptographic audit chain integrity check failed — chain was tampered with."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Audit chain integrity violated: {detail}")


# ── Multi-Agent Handoff ───────────────────────────────────────────────────────


class HandoffVerificationFailed(VeridianError):
    """Agent handoff blocked: verification checkpoint failed at the boundary."""

    def __init__(self, task_id: str, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Handoff blocked for task {task_id!r}: {reason}")


class HandoffIntegrityError(VeridianError):
    """HandoffPacket HMAC or checksum validation failed — packet was tampered with."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Handoff packet integrity check failed: {detail}")


# ── Policy Engine ─────────────────────────────────────────────────────────────


class PolicyError(VeridianError):
    """Base class for all policy engine errors."""


class PolicyCompilationError(PolicyError):
    """YAML/JSON policy definition failed to compile to a Python verifier."""

    def __init__(self, policy_id: str, reason: str) -> None:
        self.policy_id = policy_id
        self.reason = reason
        super().__init__(f"Policy {policy_id!r} compilation failed: {reason}")


class PolicyValidationError(PolicyError):
    """Policy YAML/JSON syntax or semantic validation failed."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Policy validation error in field {field!r}: {reason}")


# ── Dashboard ─────────────────────────────────────────────────────────────────


class DashboardError(VeridianError):
    """Dashboard data layer encountered an unexpected error."""


# ── Evolution Safety (Phase 7b) ──────────────────────────────────────────────


class MisevolutionDetected(VeridianError):
    """EvolutionMonitorHook: agent misevolution pathway triggered."""

    def __init__(self, pathway: str, metric: str, severity: str) -> None:
        self.pathway = pathway
        self.metric = metric
        self.severity = severity
        super().__init__(
            f"Misevolution detected — pathway={pathway}, metric={metric}, severity={severity}"
        )


class CanaryRegressionError(VeridianError):
    """Canary task suite detected a regression — evolution blocked."""

    def __init__(self, failed_canaries: list[str]) -> None:
        self.failed_canaries = failed_canaries
        summary = ", ".join(failed_canaries[:5])
        if len(failed_canaries) > 5:
            summary += f" (+{len(failed_canaries) - 5} more)"
        super().__init__(f"Canary regression: previously passing tasks now fail: {summary}")


class EvolutionBlockedError(VeridianError):
    """Evolution sandbox recommends ROLLBACK — upgrade blocked."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Evolution blocked: {reason}")


# ── Secrets Management (Phase 8) ────────────────────────────────────────────


class SecretsProviderError(VeridianError):
    """Secrets provider operation failed."""


class SecretNotFound(SecretsProviderError):
    """Required secret key not available from provider."""

    def __init__(self, secret_ref: str) -> None:
        self.secret_ref = secret_ref
        super().__init__(f"Secret not found: {secret_ref!r}")


class SecretRotationFailed(SecretsProviderError):
    """Secret rotation check failed — credentials may be stale."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Secret rotation failed: {detail}")
