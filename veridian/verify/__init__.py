"""Verification primitives, registry, and pipeline utilities."""

from veridian.verify.base import (
    BaseVerifier,
    PRMVerifier,
    VerificationResult,
    VerifierRegistry,
    registry,
)
from veridian.verify.integrity import IntegrityResult, VerifierIntegrityChecker
from veridian.verify.pipeline import (
    PipelineConfig,
    PipelineResult,
    PipelineStage,
    StageResult,
    VerificationPipeline,
)

verifier_registry = registry

__all__ = [
    "BaseVerifier",
    "PRMVerifier",
    "VerificationResult",
    "VerifierRegistry",
    "registry",
    "verifier_registry",
    "IntegrityResult",
    "VerifierIntegrityChecker",
    "PipelineConfig",
    "PipelineResult",
    "PipelineStage",
    "StageResult",
    "VerificationPipeline",
]
