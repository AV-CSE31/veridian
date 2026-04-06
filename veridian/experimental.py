"""
veridian.experimental
──────────────────────
RV3-013: Explicit namespace for unstable, pre-1.0 APIs.

Symbols re-exported here are EXPLICITLY unstable. They may change shape,
move, or be removed in any minor release. Production code should import
from ``veridian`` (stable tier) or ``veridian.integrations.sdk`` (stable
adapter SDK) instead.

Stability tiers (effective v3):
- STABLE   — ``veridian.*`` public core + ``veridian.integrations.sdk.*``
- EXPERIMENTAL — this module; import explicitly to acknowledge instability
- INTERNAL — any module path starting with ``veridian._`` or not exported

Usage::

    # Opt-in to experimental API surface
    from veridian.experimental import AdversarialEvaluator, PipelineResult

    # Will emit a DeprecationWarning at import time
    from veridian import AdversarialEvaluator  # legacy path, discouraged

As of v0.2, these symbols have been removed from the top-level
``veridian.*`` namespace. Import them from here instead.
"""

from __future__ import annotations

from veridian.contracts.hook import SprintContractHook
from veridian.contracts.sprint import ContractRegistry, SprintContract
from veridian.contracts.verifier import SprintContractVerifier

# ── Re-exports: experimental evaluator pipeline ──────────────────────────────
from veridian.eval.adversarial import AdversarialEvaluator, EvaluationResult
from veridian.eval.calibration import CalibrationProfile, GradingRubric, RubricCriterion
from veridian.eval.pipeline import PipelineResult, VerificationPipeline

# ── Re-exports: experimental GH Action surface ───────────────────────────────
from veridian.gh_action import ActionConfig, ActionResult, run_action

# ── Re-exports: experimental testing / replay ────────────────────────────────
from veridian.testing.recorder import AgentRecorder, RecordedRun
from veridian.testing.replayer import ReplayAssertion, Replayer, ReplayResult

__all__ = [
    # Adversarial + pipeline
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
    # Record / replay (Phase B ships the stable replay surface via SDK)
    "AgentRecorder",
    "RecordedRun",
    "ReplayAssertion",
    "Replayer",
    "ReplayResult",
    # GitHub Action harness
    "ActionConfig",
    "ActionResult",
    "run_action",
]


# Stability tier metadata — surface stability checks can import this dict to
# enforce "no new symbols in experimental without migration notice".
STABILITY_TIER = "experimental"
EXPERIMENTAL_SYMBOLS = frozenset(__all__)
