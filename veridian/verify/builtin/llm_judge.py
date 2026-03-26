"""
veridian.verify.builtin.llm_judge
──────────────────────────────────
LLMJudgeVerifier — use a cheap LLM to score structured output against a rubric.

IMPORTANT CONSTRAINT: LLMJudgeVerifier must NEVER be used standalone.
It must always be paired with at least one deterministic verifier inside
a CompositeVerifier. LLM judgment is probabilistic; without a deterministic
backstop, a single broken API call or hallucinated score can corrupt the run.

Usage:
    verifier_id="composite"
    verifier_config={
        "verifiers": [
            {"id": "schema",    "config": {"required_fields": ["summary", "risk_level"]}},
            {"id": "llm_judge", "config": {
                "rubric": (
                    "Does the output identify the highest-risk clause"
                    " and provide a verbatim quote?"
                ),
                "model": "gemini/gemini-2.0-flash",
                "min_score": 0.7,
            }},
        ]
    }
"""

from __future__ import annotations

import json
import logging
import re
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)

_SCORE_PATTERN = re.compile(
    r'"score"\s*:\s*(0\.\d+|1\.0|0|1)',
    re.IGNORECASE,
)


def _build_judge_prompt(rubric: str, task: Task, result: TaskResult) -> str:
    """Build the LLM judge prompt."""
    structured_preview = json.dumps(result.structured, indent=2)[:1000]
    raw_preview = result.raw_output[:800] if result.raw_output else ""
    return (
        f"You are a strict quality evaluator. Score the following agent output "
        f"from 0.0 (completely fails) to 1.0 (fully satisfies) based on the rubric below.\n\n"
        f"RUBRIC:\n{rubric}\n\n"
        f"TASK DESCRIPTION:\n{task.description[:400] or task.title}\n\n"
        f"AGENT STRUCTURED OUTPUT:\n{structured_preview}\n\n"
        f"AGENT RAW OUTPUT (excerpt):\n{raw_preview}\n\n"
        f"Respond ONLY with valid JSON. Nothing else.\n"
        f'Example: {{"score": 0.85, "reasoning": "Brief explanation under 150 chars."}}'
    )


def _parse_score(content: str) -> float | None:
    """
    Extract score from LLM response. Tries JSON parse first, falls back to regex.
    Returns float in [0.0, 1.0] or None on parse failure.
    """
    # Attempt full JSON parse
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict) and "score" in data:
            raw = float(data["score"])
            return max(0.0, min(1.0, raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Attempt JSON within content (LLM may add prose around it)
    brace_start = content.find("{")
    brace_end = content.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            data = json.loads(content[brace_start : brace_end + 1])
            if isinstance(data, dict) and "score" in data:
                raw = float(data["score"])
                return max(0.0, min(1.0, raw))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Regex fallback
    m = _SCORE_PATTERN.search(content)
    if m:
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except (ValueError, TypeError):
            pass

    return None


class LLMJudgeVerifier(BaseVerifier):
    """
    Use a cheap LLM to score agent output against a rubric.

    Passes when score >= min_score. Score is exposed via VerificationResult.score.

    MUST be used inside CompositeVerifier with at least one deterministic verifier.
    CompositeVerifier.__init__ enforces this constraint automatically.
    """

    id: ClassVar[str] = "llm_judge"
    description: ClassVar[str] = (
        "Score agent output against a rubric using a cheap LLM (0.0–1.0). "
        "Pass when score >= min_score. MUST be paired with a deterministic verifier."
    )

    def __init__(
        self,
        rubric: str,
        model: str = "gemini/gemini-2.0-flash",
        min_score: float = 0.7,
        max_tokens: int = 300,
        timeout_seconds: int = 30,
    ) -> None:
        """
        Args:
            rubric: Scoring criteria shown to the LLM. Must be non-empty.
            model: LiteLLM model string. Default gemini/gemini-2.0-flash (cheap + fast).
            min_score: Threshold in [0.0, 1.0]. Score must be >= this to pass.
            max_tokens: Maximum tokens for LLM response.
            timeout_seconds: Request timeout.

        Raises:
            VeridianConfigError: If rubric is empty or min_score is out of range.
        """
        if not rubric or not rubric.strip():
            raise VeridianConfigError(
                "LLMJudgeVerifier: 'rubric' must not be empty. "
                "Provide clear scoring criteria, e.g. 'Does the output include a verbatim quote?'."
            )
        if not 0.0 <= min_score <= 1.0:
            raise VeridianConfigError(
                f"LLMJudgeVerifier: 'min_score' must be in [0.0, 1.0], got {min_score}."
            )
        self.rubric = rubric
        self.model = model
        self.min_score = min_score
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Call LLM judge and parse score."""
        try:
            import litellm  # noqa: PLC0415
        except ImportError:
            return VerificationResult(
                passed=False,
                error="LLMJudgeVerifier requires litellm. Run: pip install litellm",
            )

        prompt = _build_judge_prompt(self.rubric, task, result)

        try:
            resp = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
                timeout=self.timeout_seconds,
                temperature=0.0,
            )
            content: str = resp.choices[0].message.content or ""
        except Exception as exc:
            log.warning("llm_judge: LLM call failed: %s", exc)
            return VerificationResult(
                passed=False,
                error=f"LLMJudgeVerifier: LLM call failed: {str(exc)[:150]}"[:300],
            )

        score = _parse_score(content)
        if score is None:
            return VerificationResult(
                passed=False,
                error=(
                    "LLMJudgeVerifier: Could not parse score from LLM response. "
                    "Expected JSON with 'score' key (0.0–1.0). Re-evaluate output manually."
                )[:300],
            )

        # Extract reasoning if available
        reasoning = ""
        try:
            data = json.loads(content.strip())
            reasoning = str(data.get("reasoning", ""))[:150]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        if score >= self.min_score:
            return VerificationResult(
                passed=True,
                score=score,
                evidence={
                    "score": score,
                    "min_score": self.min_score,
                    "reasoning": reasoning,
                    "model": self.model,
                },
            )

        error = (
            f"LLM judge score {score:.2f} below threshold {self.min_score:.2f}. "
            f"Reasoning: {reasoning or 'none provided'}. "
            f"Improve output to better satisfy: {self.rubric[:80]}"
        )[:300]

        return VerificationResult(
            passed=False,
            score=score,
            error=error,
            evidence={
                "score": score,
                "min_score": self.min_score,
                "reasoning": reasoning,
                "model": self.model,
            },
        )
