"""
veridian.eval.adversarial
──────────────────────────
AdversarialEvaluator — GAN-inspired structural separation of generator and judge.

The AdversarialEvaluator is architecturally separate from any WorkerAgent.
It receives generator output + SprintContract, applies a CalibrationProfile,
and returns an EvaluationResult with scored criteria, failure citations, and
actionable feedback.

Research basis: Anthropic's harness design research (March 2026) proved that
self-evaluation fails ~95% of the time. Structural separation via an adversarial
evaluator — like the discriminator in a GAN — drives quality upward through
competitive tension rather than collaborative agreement.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from veridian.agents.base import BaseAgent
from veridian.core.exceptions import EvaluationError
from veridian.core.task import Task, TaskResult
from veridian.eval.calibration import CalibrationProfile
from veridian.eval.sprint_contract import SprintContract
from veridian.providers.base import LLMProvider, Message

__all__ = ["AdversarialEvaluator", "EvaluationResult"]

log = logging.getLogger(__name__)

_EVAL_TAG_RE = re.compile(
    r"<veridian:eval>\s*(\{.*?\})\s*</veridian:eval>",
    re.DOTALL,
)

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "evaluator.md"


@dataclass
class EvaluationResult:
    """
    Result returned by AdversarialEvaluator.evaluate().

    passed:           True iff weighted score ≥ calibration.pass_threshold
    score:            Aggregate weighted score (0.0–1.0)
    criterion_scores: Per-criterion raw scores keyed by criterion name
    failures:         Specific failure citations (empty when passed=True)
    feedback:         Actionable feedback for the generator (≤ 2000 chars)
    iteration:        Which pipeline iteration produced this result (1-indexed)
    """

    passed: bool
    score: float
    criterion_scores: dict[str, float]
    failures: list[str]
    feedback: str
    iteration: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "passed": self.passed,
            "score": self.score,
            "criterion_scores": self.criterion_scores,
            "failures": self.failures,
            "feedback": self.feedback,
            "iteration": self.iteration,
        }


class AdversarialEvaluator(BaseAgent):
    """
    Adversarial evaluator agent — the judge in the GAN-inspired pipeline.

    MUST use an independent LLMProvider instance from any WorkerAgent (the
    generator). Structural separation is enforced by caller convention — this
    class accepts an injected provider and never shares state with the generator.

    Dependencies are injected via __init__ (no hard instantiation — see CLAUDE.md §2.1).
    """

    id: ClassVar[str] = "adversarial_evaluator"

    def __init__(
        self,
        provider: LLMProvider,
        calibration: CalibrationProfile,
    ) -> None:
        self._provider = provider
        self.calibration = calibration
        self._system_prompt: str = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Satisfy BaseAgent ABC. Use evaluate() directly."""
        return self.evaluate(*args, **kwargs)

    def evaluate(
        self,
        task: Task,
        result: TaskResult,
        contract: SprintContract,
        iteration: int = 1,
    ) -> EvaluationResult:
        """
        Evaluate generator output against the SprintContract and calibration.

        Returns EvaluationResult with pass/fail, scores, citations, and feedback.
        Raises EvaluationError if the LLM response cannot be parsed.
        """
        prompt = self._build_prompt(task, result, contract)
        messages: list[Message] = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=prompt),
        ]

        log.debug(
            "adversarial_eval.start task_id=%s iteration=%d skepticism=%.2f",
            task.id,
            iteration,
            self.calibration.skepticism,
        )

        response = self._provider.complete(messages)
        raw = response.content

        return self._parse_response(raw, iteration)

    # ── private helpers ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        task: Task,
        result: TaskResult,
        contract: SprintContract,
    ) -> str:
        """Assemble the evaluation prompt from task, result, contract, and calibration."""
        rubric_lines = "\n".join(
            f"  - {c.name} (weight={c.weight:.0%}): {c.description}"
            for c in self.calibration.rubric.criteria
        )
        deliverables = "\n".join(f"  - {d}" for d in contract.deliverables)
        criteria = "\n".join(f"  - {s}" for s in contract.success_criteria)
        tests = "\n".join(f"  - {t}" for t in contract.test_conditions) or "  (none)"

        return (
            f"## Task\n"
            f"Title: {task.title}\n"
            f"Description: {task.description}\n\n"
            f"## Sprint Contract (contract_id={contract.contract_id})\n"
            f"Deliverables:\n{deliverables}\n\n"
            f"Success criteria:\n{criteria}\n\n"
            f"Test conditions:\n{tests}\n\n"
            f"Evaluation threshold: {contract.evaluation_threshold:.0%}\n\n"
            f"## Calibration\n"
            f"Skepticism: {self.calibration.skepticism:.2f} "
            f"(0=lenient, 1=maximally critical)\n"
            f"Pass threshold: {self.calibration.pass_threshold:.0%}\n\n"
            f"## Grading Rubric: {self.calibration.rubric.name}\n"
            f"{rubric_lines}\n\n"
            f"## Generator Output\n"
            f"{result.raw_output[:8000]}\n\n"
            f"## Structured Claims\n"
            f"{json.dumps(result.structured, indent=2)[:4000]}\n\n"
            f"Now evaluate the output. Return a single <veridian:eval> block."
        )

    def _parse_response(self, raw: str, iteration: int) -> EvaluationResult:
        """
        Parse the LLM response into an EvaluationResult.

        Raises EvaluationError with a clear message if the response is malformed.
        """
        match = _EVAL_TAG_RE.search(raw)
        if not match:
            raise EvaluationError(
                f"Could not parse adversarial evaluator response: "
                f"no <veridian:eval>...</veridian:eval> block found. "
                f"Raw output (first 300 chars): {raw[:300]!r}"
            )

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise EvaluationError(
                f"Could not parse JSON inside <veridian:eval> block: {exc}. "
                f"Ensure the evaluator LLM returns valid JSON."
            ) from exc

        # Validate required fields
        required = {"passed", "score", "criterion_scores", "failures", "feedback"}
        missing = required - set(data.keys())
        if missing:
            raise EvaluationError(
                f"Evaluator response missing required fields: {sorted(missing)}. "
                f"The evaluator must return all of: {sorted(required)}."
            )

        criterion_scores: dict[str, float] = data["criterion_scores"]
        failures: list[str] = data.get("failures") or []
        feedback: str = str(data.get("feedback", ""))[:2000]

        # Recompute score from criterion_scores + calibration to avoid
        # trusting LLM arithmetic
        try:
            score = self.calibration.compute_weighted_score(criterion_scores)
        except Exception:
            # Fall back to LLM-reported score if criterion_scores incomplete
            score = float(data.get("score", 0.0))

        passed = score >= self.calibration.pass_threshold

        log.debug(
            "adversarial_eval.result iteration=%d passed=%s score=%.3f",
            iteration,
            passed,
            score,
        )

        return EvaluationResult(
            passed=passed,
            score=score,
            criterion_scores=criterion_scores,
            failures=failures,
            feedback=feedback,
            iteration=iteration,
        )
