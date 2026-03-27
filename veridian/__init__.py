"""
veridian
────────
Production-grade infrastructure for reliable long-running AI agents.

The missing primitive: the verification contract between an agent and the world.

Quick start::

    from veridian import TaskLedger, Task, VeridianRunner, LiteLLMProvider

    provider = LiteLLMProvider()   # reads VERIDIAN_MODEL env var

    ledger = TaskLedger("ledger.json")
    ledger.add([
        Task(
            title="Migrate auth.py to Python 3.11",
            description=(
                "Migrate src/auth.py to Python 3.11 syntax. "
                "Run: pytest tests/test_auth.py -v. "
                "Verify: all tests pass."
            ),
            verifier_id="bash_exit",
            verifier_config={"command": "pytest tests/test_auth.py -v"},
        )
    ])

    summary = VeridianRunner(ledger=ledger, provider=provider).run()
    print(f"Done: {summary.done}/{summary.total}")

GitHub:  https://github.com/veridian-ai/veridian
Docs:    https://veridian.readthedocs.io
PyPI:    https://pypi.org/project/veridian/
License: MIT
"""

__version__ = "0.1.0"
__author__ = "Veridian contributors"
__license__ = "MIT"

# ── Core domain models ─────────────────────────────────────────────────────────
# ── A7: Budget primitives ──────────────────────────────────────────────────────
from veridian.budget import Budget, BudgetState
from veridian.core.events import (
    CircuitBreakerClosed,
    CircuitBreakerOpened,
    ContextCompacted,
    ContractNegotiated,
    ContractSigned,
    ContractViolated,
    CostGuardTriggered,
    CostWarning,
    EvaluationCompleted,
    EvaluationConverged,
    EvaluationExhausted,
    EvaluationStarted,
    HumanReviewRequested,
    HumanReviewResumed,
    RateLimitHit,
    RetryScheduled,
    RunCompleted,
    RunStarted,
    SLABreached,
    SLAWarning,
    TaskAbandoned,
    TaskClaimed,
    TaskCompleted,
    TaskFailed,
    TaskSkipped,
    VeridianEvent,
    VerificationFailed,
    VerificationPassed,
)

# Import RunAborted from events and alias to avoid clash with the exception
from veridian.core.events import RunAborted as RunAbortedEvent
from veridian.core.exceptions import (
    BlockedCommand,
    BudgetExceeded,
    CalibrationError,
    ContextWindowExceeded,
    ContractNotFound,
    ContractViolation,
    CostLimitExceeded,
    EvaluationError,
    ExecutorError,
    ExecutorTimeout,
    HumanReviewRequired,
    InvalidTransition,
    LedgerCorrupted,
    ProviderError,
    ProviderRateLimited,
    RunAborted,
    TaskAlreadyClaimed,
    TaskNotFound,
    VeridianConfigError,
    VeridianError,
    VerificationError,
    VerifierNotFound,
)

# ── Task quality gate ──────────────────────────────────────────────────────────
from veridian.core.quality_gate import (
    QualityScore,
    TaskGraph,
    TaskQualityGate,
)
from veridian.core.task import (
    LedgerStats,
    Task,
    TaskPriority,
    TaskResult,
    TaskStatus,
)

# ── A3: Cost tracking ──────────────────────────────────────────────────────────
from veridian.cost import (
    BUILTIN_PRICING,
    CostEntry,
    CostTracker,
    ModelPricing,
    compute_cost,
)

# ── Decorator ──────────────────────────────────────────────────────────────────
from veridian.decorator import verified

# ── Ledger ─────────────────────────────────────────────────────────────────────
from veridian.ledger.ledger import TaskLedger

# ── A2: OTel OTLP exporter ─────────────────────────────────────────────────────
from veridian.observability.otlp_exporter import (
    OTLPConfig,
    VerificationSpan,
    configure_otlp_tracer,
)

# ── Providers ──────────────────────────────────────────────────────────────────
from veridian.providers.base import LLMProvider, LLMResponse, Message
from veridian.providers.litellm_provider import CircuitBreaker, LiteLLMProvider
from veridian.providers.mock_provider import MockProvider

# Import builtin verifiers so they self-register on `import veridian`
from veridian.verify import builtin as _builtin_verifiers  # noqa: F401

# ── Verification ───────────────────────────────────────────────────────────────
from veridian.verify.base import (
    BaseVerifier,
    VerificationResult,
)
from veridian.verify.base import (
    registry as verifier_registry,
)


# ── Runner (lazy-loaded to avoid circular imports at the top level) ────────────
def __getattr__(name: str) -> object:
    """
    Lazy-load heavy runner and agent modules on first access.
    This keeps `import veridian` fast and avoids circular import issues.
    """
    if name in ("VeridianRunner", "VeridianConfig", "RunSummary"):
        from veridian.core.config import VeridianConfig  # noqa: PLC0415
        from veridian.loop.runner import (  # noqa: PLC0415
            RunSummary,
            VeridianRunner,
        )

        globals()["VeridianRunner"] = VeridianRunner
        globals()["VeridianConfig"] = VeridianConfig
        globals()["RunSummary"] = RunSummary
        return globals()[name]

    if name == "ParallelRunner":
        from veridian.loop.parallel_runner import ParallelRunner  # noqa: PLC0415

        globals()["ParallelRunner"] = ParallelRunner
        return ParallelRunner

    if name in ("InitializerAgent", "WorkerAgent"):
        from veridian.agents.initializer import InitializerAgent  # noqa: PLC0415
        from veridian.agents.worker import WorkerAgent  # noqa: PLC0415

        globals()["InitializerAgent"] = InitializerAgent
        globals()["WorkerAgent"] = WorkerAgent
        return globals()[name]

    if name in ("BaseHook", "HookRegistry"):
        from veridian.hooks.base import BaseHook  # noqa: PLC0415
        from veridian.hooks.registry import HookRegistry  # noqa: PLC0415

        globals()["BaseHook"] = BaseHook
        globals()["HookRegistry"] = HookRegistry
        return globals()[name]

    if name in (
        "LoggingHook",
        "CostGuardHook",
        "HumanReviewHook",
        "RateLimitHook",
        "SlackNotifyHook",
    ):
        import veridian.hooks.builtin as _hooks  # noqa: PLC0415

        return getattr(_hooks, name)

    if name == "CrossRunConsistencyHook":
        from veridian.hooks.builtin.cross_run_consistency import (  # noqa: PLC0415
            CrossRunConsistencyHook,
        )

        globals()["CrossRunConsistencyHook"] = CrossRunConsistencyHook
        return CrossRunConsistencyHook

    if name == "VeridianTracer":
        from veridian.observability.tracer import VeridianTracer  # noqa: PLC0415

        globals()["VeridianTracer"] = VeridianTracer
        return VeridianTracer

    if name == "EntropyGC":
        from veridian.entropy.gc import EntropyGC  # noqa: PLC0415

        globals()["EntropyGC"] = EntropyGC
        return EntropyGC

    if name in ("SemanticGroundingVerifier", "ConfidenceScore", "SelfConsistencyVerifier"):
        from veridian.verify.builtin.confidence import (  # noqa: PLC0415
            ConfidenceScore,
            SelfConsistencyVerifier,
        )
        from veridian.verify.builtin.semantic_grounding import (  # noqa: PLC0415
            SemanticGroundingVerifier,
        )

        globals()["SemanticGroundingVerifier"] = SemanticGroundingVerifier
        globals()["ConfidenceScore"] = ConfidenceScore
        globals()["SelfConsistencyVerifier"] = SelfConsistencyVerifier
        return globals()[name]

    if name in ("TrustedExecutor", "OutputSanitizer", "BashOutput"):
        from veridian.loop.trusted_executor import (  # noqa: PLC0415
            BashOutput,
            OutputSanitizer,
            TrustedExecutor,
        )

        globals()["TrustedExecutor"] = TrustedExecutor
        globals()["OutputSanitizer"] = OutputSanitizer
        globals()["BashOutput"] = BashOutput
        return globals()[name]

    if name in (
        "SprintContract",
        "ContractRegistry",
        "SprintContractVerifier",
        "SprintContractHook",
    ):
        from veridian.contracts import (  # noqa: PLC0415
            ContractRegistry,
            SprintContract,
            SprintContractHook,
            SprintContractVerifier,
        )

        globals()["SprintContract"] = SprintContract
        globals()["ContractRegistry"] = ContractRegistry
        globals()["SprintContractVerifier"] = SprintContractVerifier
        globals()["SprintContractHook"] = SprintContractHook
        return globals()[name]

    if name in (
        "AdversarialEvaluator",
        "EvaluationResult",
        "CalibrationProfile",
        "GradingRubric",
        "RubricCriterion",
        "PipelineResult",
        "VerificationPipeline",
    ):
        from veridian.eval.adversarial import (  # noqa: PLC0415
            AdversarialEvaluator,
            EvaluationResult,
        )
        from veridian.eval.calibration import (  # noqa: PLC0415
            CalibrationProfile,
            GradingRubric,
            RubricCriterion,
        )
        from veridian.eval.pipeline import PipelineResult, VerificationPipeline  # noqa: PLC0415

        for _n, _v in [
            ("AdversarialEvaluator", AdversarialEvaluator),
            ("EvaluationResult", EvaluationResult),
            ("CalibrationProfile", CalibrationProfile),
            ("GradingRubric", GradingRubric),
            ("RubricCriterion", RubricCriterion),
            ("PipelineResult", PipelineResult),
            ("VerificationPipeline", VerificationPipeline),
        ]:
            globals()[_n] = _v
        return globals()[name]

    if name in (
        "AgentRecorder",
        "RecordedRun",
        "ReplayAssertion",
        "ReplayResult",
        "Replayer",
    ):
        from veridian.testing.recorder import AgentRecorder, RecordedRun  # noqa: PLC0415
        from veridian.testing.replayer import (  # noqa: PLC0415
            ReplayAssertion,
            Replayer,
            ReplayResult,
        )

        globals()["AgentRecorder"] = AgentRecorder
        globals()["RecordedRun"] = RecordedRun
        globals()["ReplayAssertion"] = ReplayAssertion
        globals()["ReplayResult"] = ReplayResult
        globals()["Replayer"] = Replayer
        return globals()[name]

    if name in ("ActionConfig", "ActionResult", "run_action"):
        from veridian.gh_action import ActionConfig, ActionResult, run_action  # noqa: PLC0415

        globals()["ActionConfig"] = ActionConfig
        globals()["ActionResult"] = ActionResult
        globals()["run_action"] = run_action
        return globals()[name]

    if name == "EmbeddingGroundingVerifier":
        from veridian.verify.builtin.embedding_grounding import (  # noqa: PLC0415
            EmbeddingGroundingVerifier,
        )

        globals()["EmbeddingGroundingVerifier"] = EmbeddingGroundingVerifier
        return EmbeddingGroundingVerifier

    raise AttributeError(f"module 'veridian' has no attribute {name!r}")


__all__ = [
    # Version
    "__version__",
    # Core models
    "Task",
    "TaskStatus",
    "TaskResult",
    "TaskPriority",
    "LedgerStats",
    # Events
    "VeridianEvent",
    "RunStarted",
    "RunCompleted",
    "RunAbortedEvent",
    "TaskClaimed",
    "TaskCompleted",
    "TaskFailed",
    "TaskAbandoned",
    "TaskSkipped",
    "VerificationPassed",
    "VerificationFailed",
    "CircuitBreakerOpened",
    "CircuitBreakerClosed",
    "CostGuardTriggered",
    "CostWarning",
    "RateLimitHit",
    "RetryScheduled",
    "HumanReviewRequested",
    "HumanReviewResumed",
    "SLAWarning",
    "SLABreached",
    "ContextCompacted",
    "ContractSigned",
    "ContractViolated",
    "ContractNegotiated",
    "EvaluationStarted",
    "EvaluationCompleted",
    "EvaluationConverged",
    "EvaluationExhausted",
    # Exceptions
    "VeridianError",
    "VeridianConfigError",
    "InvalidTransition",
    "LedgerCorrupted",
    "TaskNotFound",
    "TaskAlreadyClaimed",
    "VerificationError",
    "VerifierNotFound",
    "ProviderError",
    "ProviderRateLimited",
    "ContextWindowExceeded",
    "ExecutorError",
    "ExecutorTimeout",
    "BlockedCommand",
    "CostLimitExceeded",
    "HumanReviewRequired",
    "RunAborted",
    "ContractViolation",
    "ContractNotFound",
    "EvaluationError",
    "CalibrationError",
    # Quality gate
    "TaskQualityGate",
    "TaskGraph",
    "QualityScore",
    # Ledger
    "TaskLedger",
    # Verification
    "BaseVerifier",
    "VerificationResult",
    "verifier_registry",
    # Providers
    "LLMProvider",
    "LLMResponse",
    "Message",
    "LiteLLMProvider",
    "CircuitBreaker",
    "MockProvider",
    # Decorator
    "verified",
    # A2: OTel OTLP exporter
    "OTLPConfig",
    "VerificationSpan",
    "configure_otlp_tracer",
    # A3: Cost tracking
    "BUILTIN_PRICING",
    "CostEntry",
    "CostTracker",
    "ModelPricing",
    "compute_cost",
    # A7: Budget primitives
    "Budget",
    "BudgetState",
    "BudgetExceeded",
    # Lazy-loaded (via __getattr__)
    "VeridianRunner",
    "VeridianConfig",
    "RunSummary",
    "ParallelRunner",
    "InitializerAgent",
    "WorkerAgent",
    "BaseHook",
    "HookRegistry",
    "LoggingHook",
    "CostGuardHook",
    "HumanReviewHook",
    "RateLimitHook",
    "SlackNotifyHook",
    "CrossRunConsistencyHook",
    "VeridianTracer",
    "EntropyGC",
    "SemanticGroundingVerifier",
    "ConfidenceScore",
    "SelfConsistencyVerifier",
    "TrustedExecutor",
    "OutputSanitizer",
    "BashOutput",
    # Sprint Contract Protocol
    "SprintContract",
    "ContractRegistry",
    "SprintContractVerifier",
    "SprintContractHook",
    # Adversarial Evaluator Pipeline
    "AdversarialEvaluator",
    "EvaluationResult",
    "CalibrationProfile",
    "GradingRubric",
    "RubricCriterion",
    "PipelineResult",
    "VerificationPipeline",
    # A4: Testing / replay
    "AgentRecorder",
    "RecordedRun",
    "ReplayAssertion",
    "ReplayResult",
    "Replayer",
    # A5: GitHub Action
    "ActionConfig",
    "ActionResult",
    "run_action",
    # A6: Embedding grounding verifier
    "EmbeddingGroundingVerifier",
]
