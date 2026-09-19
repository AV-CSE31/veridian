"""Lightweight confidence scoring for runner metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfidenceScore:
    """Multi-dimensional confidence estimate attached to a TaskResult."""

    attempt_score: float = 1.0
    verifier_score: float = 1.0
    consistency_score: float = 1.0
    composite: float = 1.0
    tier: str = "HIGH"

    _WEIGHTS = {
        "attempt_score": 0.35,
        "verifier_score": 0.40,
        "consistency_score": 0.25,
    }

    @classmethod
    def compute(
        cls,
        retry_count: int,
        max_retries: int,
        verifier_score: float | None = None,
        consistency_score: float | None = None,
    ) -> ConfidenceScore:
        """Compute a bounded confidence score from retry and verifier signals."""
        if max_retries <= 0:
            attempt_score = 1.0 if retry_count <= 0 else 0.1
        else:
            attempt_score = max(0.1, 1.0 - (retry_count / max_retries))
        vs = verifier_score if verifier_score is not None else 1.0
        cs = consistency_score if consistency_score is not None else 1.0

        weights = cls._WEIGHTS
        composite = (
            attempt_score ** weights["attempt_score"]
            * vs ** weights["verifier_score"]
            * cs ** weights["consistency_score"]
        )
        composite = round(min(1.0, max(0.0, composite)), 3)

        if composite >= 0.85:
            tier = "HIGH"
        elif composite >= 0.65:
            tier = "MEDIUM"
        elif composite >= 0.40:
            tier = "LOW"
        else:
            tier = "UNCERTAIN"

        return cls(
            attempt_score=round(attempt_score, 3),
            verifier_score=round(vs, 3),
            consistency_score=round(cs, 3),
            composite=composite,
            tier=tier,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "attempt_score": self.attempt_score,
            "verifier_score": self.verifier_score,
            "consistency_score": self.consistency_score,
            "composite": self.composite,
            "tier": self.tier,
        }
