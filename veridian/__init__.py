"""
veridian
────────
Deterministic verification and replay-safe runtime for agent workflows.

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
    print(f"Done: {summary.done_count}/{summary.total_tasks}")

GitHub:  https://github.com/AV-CSE31/veridian
PyPI:    https://pypi.org/project/veridian-ai/
License: MIT
"""

__version__ = "0.3.0"
__author__ = "Veridian contributors"
__license__ = "MIT"

# ── Stable eager imports (v0.2 minimized surface) ────────────────────────────
# Only symbols listed in __all__ are imported here. Everything else is
# importable from its module path (e.g. ``from veridian.core.events import ...``).

# Budget
from veridian.budget import Budget, BudgetState

# Events (lifecycle core only)
from veridian.core.events import (
    RunCompleted,
    RunStarted,
    TaskClaimed,
    TaskCompleted,
    TaskFailed,
    VeridianEvent,
)

# Exceptions (core only)
from veridian.core.exceptions import (
    BudgetExceeded,
    CostLimitExceeded,
    HumanReviewRequired,
    InvalidTransition,
    ProviderError,
    TaskAlreadyClaimed,
    TaskNotFound,
    VeridianConfigError,
    VeridianError,
    VerificationError,
    VerifierNotFound,
)

# Task domain
from veridian.core.task import (
    LedgerStats,
    Task,
    TaskPriority,
    TaskResult,
    TaskStatus,
)

# Ledger
from veridian.ledger.ledger import TaskLedger

# Providers
from veridian.providers.base import LLMProvider, LLMResponse, Message
from veridian.providers.litellm_provider import LiteLLMProvider
from veridian.providers.mock_provider import MockProvider

# Import builtin verifiers so they self-register on `import veridian`
from veridian.verify import builtin as _builtin_verifiers  # noqa: F401

# Verification
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.base import registry as verifier_registry

# ── Deprecated / experimental symbols (RV3-013 + audit F2) ───────────────────
#
# These symbols remain importable from the top-level ``veridian`` namespace in
# v3 for backward compatibility, but the stable home is ``veridian.experimental``
# and they will be removed from ``veridian.*`` in a future release. Every access emits a
# DeprecationWarning with the migration path so downstream code upgrades
# deterministically instead of discovering the change at release time.

_DEPRECATED_EXPERIMENTAL_SYMBOLS: frozenset[str] = frozenset(
    {
        # Adversarial evaluation pipeline
        "AdversarialEvaluator",
        "EvaluationResult",
        "CalibrationProfile",
        "GradingRubric",
        "RubricCriterion",
        "PipelineResult",
        "VerificationPipeline",
        # Sprint Contract Protocol
        "SprintContract",
        "ContractRegistry",
        "SprintContractVerifier",
        "SprintContractHook",
        # Record/replay harness
        "AgentRecorder",
        "RecordedRun",
        "ReplayAssertion",
        "ReplayResult",
        "Replayer",
        # GitHub Action harness
        "ActionConfig",
        "ActionResult",
        "run_action",
    }
)


# ── Runner (lazy-loaded to avoid circular imports at the top level) ────────────
def __getattr__(name: str) -> object:
    """
    Lazy-load heavy runner and agent modules on first access.
    This keeps `import veridian` fast and avoids circular import issues.

    Removed experimental symbols (see ``_DEPRECATED_EXPERIMENTAL_SYMBOLS``)
    fail closed with ``AttributeError`` and a migration hint to
    ``veridian.experimental.*``.
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

    # ── v0.2 breaking change: removed deprecated experimental symbols ────────
    # These were accessible from ``veridian.*`` in v3 with a DeprecationWarning.
    # In v0.2 they are only available from ``veridian.experimental.*``.
    if name in _DEPRECATED_EXPERIMENTAL_SYMBOLS:
        raise AttributeError(
            f"module 'veridian' no longer exports {name!r} (removed in v0.2). "
            f"Import from veridian.experimental instead: "
            f"`from veridian.experimental import {name}`"
        )

    raise AttributeError(f"module 'veridian' has no attribute {name!r}")


# ── Stable public API (v0.2 — minimized per Phase D of 09-foundation-cleanup) ─
# Target: ~40 symbols (down from 123 in v3). Everything else is importable
# from its module path but not advertised here.
__all__ = [
    # Version
    "__version__",
    # Core domain models
    "Task",
    "TaskStatus",
    "TaskResult",
    "TaskPriority",
    "LedgerStats",
    # Ledger
    "TaskLedger",
    # Runner
    "VeridianRunner",
    "VeridianConfig",
    "RunSummary",
    "ParallelRunner",
    # Verification
    "BaseVerifier",
    "VerificationResult",
    "verifier_registry",
    # Hooks
    "BaseHook",
    "HookRegistry",
    # Providers
    "LLMProvider",
    "LLMResponse",
    "Message",
    "LiteLLMProvider",
    "MockProvider",
    # Events (lifecycle)
    "VeridianEvent",
    "RunStarted",
    "RunCompleted",
    "TaskClaimed",
    "TaskCompleted",
    "TaskFailed",
    # Exceptions (core)
    "VeridianError",
    "VeridianConfigError",
    "InvalidTransition",
    "TaskNotFound",
    "TaskAlreadyClaimed",
    "VerificationError",
    "VerifierNotFound",
    "ProviderError",
    "HumanReviewRequired",
    "CostLimitExceeded",
    "BudgetExceeded",
    # Budget
    "Budget",
    "BudgetState",
]
# Count: 40 symbols (was 123 in v3).
# Everything removed here is still importable from its module path (e.g.
# ``from veridian.core.events import SLAWarning``). See planning/MIGRATION_v3_to_v4.md.
