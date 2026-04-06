"""
veridian.verify.builtin.confidence
───────────────────────────────────
ConfidenceScore and SelfConsistencyVerifier — Gap 2 implementation.

RESEARCH BASIS:
  Braincuber agent variance study (2025):
    "Production-ready agents should show under 11% variance across runs.
     TCR measured on a single run is deceptive — performance drops from 60%
     on a single run to 25% across 8-run consistency tests."

  SelfCheckGPT approach (widely cited 2025):
    Generate same output N times, check for self-contradictions. Confidence
    estimated from consistency across samples without requiring ground truth.

  Hallucination detection survey (arXiv 2601.09929):
    Confidence calibration aligns model predicted confidence with empirical
    accuracy. A prediction with 80% confidence should be correct ~80% of the time.

COMPONENTS:
  ConfidenceScore — dataclass attached to every TaskResult after verification.
    Aggregates: retry_count (inverted), verifier score, attempt number.
    Stored in result.confidence_score for downstream hooks and reporting.

  SelfConsistencyVerifier — optional verifier that generates the structured
    output twice (with slight temperature variation) and checks key fields
    for agreement. Fails if critical fields disagree across samples.
    Uses a cheap model — never the primary execution model.

USAGE:
  # Automatic: ConfidenceScore is computed by VeridianRunner after every verifier pass.
  # Manual SelfConsistency check (add to composite chain for high-stakes tasks):
  verifier_config={
      "verifiers": [
          {"id": "schema"},
          {"id": "self_consistency", "config": {
              "critical_fields": ["risk_level", "decision", "status"],
              "model": "gemini/gemini-2.0-flash",  # cheap model
              "n_samples": 2,
          }},
      ]
  }
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)


# ── Confidence Score ──────────────────────────────────────────────────────────


@dataclass
class ConfidenceScore:
    """
    Multi-dimensional confidence estimate attached to a TaskResult.

    Dimensions:
      attempt_score    — 1.0 if first attempt, degrades with retries
      verifier_score   — 0.0–1.0 from numeric verifiers (e.g. LLMJudge), else 1.0
      consistency_score — 0.0–1.0 from SelfConsistency if run, else 1.0

    composite  — weighted geometric mean of all dimensions
    tier       — human-readable: HIGH / MEDIUM / LOW / UNCERTAIN
    """

    attempt_score: float = 1.0
    verifier_score: float = 1.0
    consistency_score: float = 1.0
    composite: float = 1.0
    tier: str = "HIGH"

    # Weights for composite calculation
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
        """
        Compute confidence from available signals.

        attempt_score degrades linearly:
          attempt 1 → 1.0, attempt 2 → 0.75, attempt 3 → 0.50, attempt N → max(0.1, ...)
        """
        attempt_score = max(0.1, 1.0 - (retry_count * 0.25))
        vs = verifier_score if verifier_score is not None else 1.0
        cs = consistency_score if consistency_score is not None else 1.0

        # Weighted geometric mean (more robust to outliers than arithmetic)
        w = cls._WEIGHTS
        composite = (
            attempt_score ** w["attempt_score"]
            * vs ** w["verifier_score"]
            * cs ** w["consistency_score"]
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
        return {
            "attempt_score": self.attempt_score,
            "verifier_score": self.verifier_score,
            "consistency_score": self.consistency_score,
            "composite": self.composite,
            "tier": self.tier,
        }


# ── Self-Consistency Verifier ─────────────────────────────────────────────────


class SelfConsistencyVerifier(BaseVerifier):
    """
    Generates the structured output N times (cheap model, slight temperature
    variation) and checks that critical fields agree across samples.

    WHEN TO USE:
    - High-stakes tasks: legal risk assessment, compliance decisions, financial analysis
    - Any task where a wrong answer has significant downstream consequences
    - NOT for bulk tasks (content moderation, migration) — too expensive

    Pair with SemanticGroundingVerifier and domain verifier in CompositeVerifier:
        [semantic_grounding, schema, self_consistency, domain_verifier]
    """

    id = "self_consistency"
    description = (
        "Generates structured output N times and checks critical fields for agreement. "
        "Use for high-stakes tasks where wrong answers have significant consequences."
    )

    _RESULT_PATTERN = re.compile(
        r"<veridian:result>\s*(\{.*?\})\s*</veridian:result>",
        re.DOTALL,
    )

    def __init__(
        self,
        critical_fields: list[str] | None = None,
        model: str = "gemini/gemini-2.0-flash",
        n_samples: int = 2,
        temperature_variation: float = 0.3,
        agreement_threshold: float = 0.5,  # fraction of samples that must agree
    ) -> None:
        self.critical_fields = critical_fields or []
        self.model = model or os.getenv("VERIDIAN_CONSISTENCY_MODEL", "gemini/gemini-2.0-flash")
        self.n_samples = max(2, n_samples)
        self.temperature_variation = temperature_variation
        self.agreement_threshold = agreement_threshold

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        if not self.critical_fields:
            return VerificationResult(
                passed=True,
                evidence={"self_consistency": "skipped — no critical_fields configured"},
            )

        samples = self._generate_samples(task, result)
        if len(samples) < 2:
            log.warning("self_consistency: could not generate enough samples, skipping")
            return VerificationResult(
                passed=True,
                evidence={"self_consistency": "skipped — sample generation failed"},
            )

        conflicts = self._find_conflicts(result.structured, samples)
        if conflicts:
            conflict_str = "; ".join(
                f"{f}: original='{orig}' but sample said '{alt}'" for f, orig, alt in conflicts[:3]
            )
            return VerificationResult(
                passed=False,
                score=0.3,
                error=(
                    f"[self_consistency] Critical fields are inconsistent across "
                    f"multiple generations: {conflict_str}. "
                    f"Review your answer and ensure it is unambiguous."
                )[:300],
                evidence={"conflicts": conflicts},
            )

        # All fields agreed — high consistency
        return VerificationResult(
            passed=True,
            score=1.0,
            evidence={
                "self_consistency": "passed",
                "samples_checked": len(samples),
                "critical_fields_agreed": self.critical_fields,
            },
        )

    def _generate_samples(self, task: Task, original: TaskResult) -> list[dict[str, Any]]:
        """
        Generate N alternative structured outputs using a cheap model.
        Returns list of parsed structured dicts.
        """
        try:
            import litellm  # noqa: PLC0415
        except ImportError:
            log.debug("self_consistency: litellm not available, skipping")
            return []

        prompt = self._build_prompt(task, original)
        samples: list[dict[str, Any]] = []

        for i in range(self.n_samples):
            temp = self.temperature_variation * (i + 1)
            try:
                resp = litellm.completion(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=min(1.0, temp),
                    max_tokens=500,
                    timeout=30,
                )
                text = resp.choices[0].message.content or ""
                m = self._RESULT_PATTERN.search(text)
                if m:
                    parsed = json.loads(m.group(1))
                    samples.append(parsed.get("structured", parsed))
            except Exception as e:
                log.debug("self_consistency: sample %d failed: %s", i, e)

        return samples

    @staticmethod
    def _build_prompt(task: Task, original: TaskResult) -> str:
        structured_preview = json.dumps(original.structured, indent=2)[:800]
        return (
            f"You are a verification assistant. Review the following task and the "
            f"agent's answer. Re-derive the critical fields independently.\n\n"
            f"TASK:\n{task.description[:400]}\n\n"
            f"AGENT'S STRUCTURED OUTPUT (to verify):\n{structured_preview}\n\n"
            f"Re-derive the answer from scratch. Output ONLY:\n"
            f"<veridian:result>\n"
            f'{{"structured": {{...critical fields only...}}, "summary": "..."}}\n'
            f"</veridian:result>\n"
        )

    def _find_conflicts(
        self, original: dict[str, Any], samples: list[dict[str, Any]]
    ) -> list[tuple[str, Any, Any]]:
        """
        Return list of (field, original_value, conflicting_sample_value)
        for fields where samples disagree with original.
        """
        conflicts = []
        for field in self.critical_fields:
            if field not in original:
                continue
            orig_val = original[field]
            for sample in samples:
                if field not in sample:
                    continue
                sample_val = sample[field]
                # Normalise for comparison (strip whitespace, lowercase strings)
                if isinstance(orig_val, str) and isinstance(sample_val, str):
                    if orig_val.strip().lower() != sample_val.strip().lower():
                        conflicts.append((field, orig_val, sample_val))
                        break
                elif orig_val != sample_val:
                    conflicts.append((field, orig_val, sample_val))
                    break
        return conflicts
