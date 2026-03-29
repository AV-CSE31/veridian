"""
veridian.eval
──────────────
Adversarial Evaluator Pipeline — GAN-inspired structural separation of
generator and judge for reliable AI agent output verification.

Public API::

    from veridian.eval import (
        AdversarialEvaluator,
        CalibrationProfile,
        EvaluationResult,
        GradingRubric,
        PipelineResult,
        RubricCriterion,
        SprintContract,
        VerificationPipeline,
    )

Research basis: Anthropic harness design (March 2026) — self-evaluation fails
~95% of the time. Structural separation via adversarial tension drives quality.
"""

from veridian.eval.adversarial import AdversarialEvaluator, EvaluationResult
from veridian.eval.calibration import CalibrationProfile, GradingRubric, RubricCriterion
from veridian.eval.canary import CanaryReport, CanaryResult, CanarySuite, CanaryTask
from veridian.eval.comparator import ComparisonResult, EvolutionComparator
from veridian.eval.pipeline import PipelineResult, VerificationPipeline
from veridian.eval.sandbox import EvolutionSandbox, SandboxResult
from veridian.eval.sprint_contract import SprintContract

__all__ = [
    "AdversarialEvaluator",
    "CalibrationProfile",
    "CanaryReport",
    "CanaryResult",
    "CanarySuite",
    "CanaryTask",
    "ComparisonResult",
    "EvaluationResult",
    "EvolutionComparator",
    "EvolutionSandbox",
    "GradingRubric",
    "PipelineResult",
    "RubricCriterion",
    "SandboxResult",
    "SprintContract",
    "VerificationPipeline",
]
