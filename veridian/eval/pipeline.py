"""
veridian.eval.pipeline
───────────────────────
VerificationPipeline — orchestrates the generator → adversarial evaluator loop.

The pipeline:
  1. Validates the SprintContract is fully signed (both generator + evaluator)
  2. Runs the adversarial evaluator against the current generator output
  3. If evaluation fails and max_iterations not reached, allows the caller to
     provide updated generator output for the next iteration
  4. Returns PipelineResult with full iteration history, convergence flag,
     and best score across all iterations

Events fired (via HookRegistry if provided):
  ContractNegotiated  — on entry, confirms signed contract
  EvaluationStarted   — before each iteration
  EvaluationCompleted — after each iteration
  EvaluationConverged — on first passing evaluation
  EvaluationExhausted — when max_iterations reached without convergence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from veridian.core.events import (
    EvaluationCompleted,
    EvaluationConverged,
    EvaluationExhausted,
    EvaluationStarted,
)
from veridian.core.exceptions import ContractViolation
from veridian.core.task import Task, TaskResult
from veridian.eval.adversarial import AdversarialEvaluator, EvaluationResult
from veridian.eval.sprint_contract import SprintContract

__all__ = ["PipelineResult", "VerificationPipeline"]

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """
    Aggregate result of a full VerificationPipeline run.

    converged:    True iff at least one iteration produced a passing score
    iterations:   Number of evaluation iterations performed
    eval_history: EvaluationResult per iteration (index 0 = iteration 1)
    best_score:   Highest score achieved across all iterations
    final_eval:   The last EvaluationResult (use this for downstream decisions)
    """

    converged: bool
    iterations: int
    eval_history: list[EvaluationResult]
    best_score: float
    final_eval: EvaluationResult

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "converged": self.converged,
            "iterations": self.iterations,
            "best_score": self.best_score,
            "eval_history": [e.to_dict() for e in self.eval_history],
            "final_eval": self.final_eval.to_dict(),
        }


class VerificationPipeline:
    """
    Orchestrates the generator → adversarial-evaluator feedback loop.

    The pipeline is stateless — each call to run() is independent.
    Dependencies are injected via __init__ (CLAUDE.md §2.1).

    Args:
        evaluator:       AdversarialEvaluator instance (independent provider)
        max_iterations:  Maximum evaluation rounds before declaring exhaustion
        hooks:           Optional HookRegistry for lifecycle event firing
    """

    def __init__(
        self,
        evaluator: AdversarialEvaluator,
        max_iterations: int = 3,
        hooks: Any | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.max_iterations = max_iterations
        self._hooks = hooks

    def run(
        self,
        task: Task,
        result: TaskResult,
        contract: SprintContract,
        run_id: str = "",
    ) -> PipelineResult:
        """
        Run the adversarial evaluation loop.

        Args:
            task:     The task being evaluated
            result:   Current generator output
            contract: SprintContract (must be fully signed)
            run_id:   Optional run identifier for event metadata

        Returns:
            PipelineResult with full iteration history

        Raises:
            ContractViolation: If the contract is not fully signed
        """
        if not contract.is_signed():
            raise ContractViolation(
                contract_id=contract.contract_id,
                reason=(
                    "contract is not fully signed — both generator and evaluator "
                    "must call sign_generator() and sign_evaluator() before running"
                ),
            )

        eval_history: list[EvaluationResult] = []
        best_score = 0.0

        for iteration in range(1, self.max_iterations + 1):
            self._fire(
                EvaluationStarted(
                    run_id=run_id,
                    task_id=task.id,
                    contract_id=contract.contract_id,
                    iteration=iteration,
                )
            )

            log.debug(
                "pipeline.iteration task_id=%s iteration=%d/%d",
                task.id,
                iteration,
                self.max_iterations,
            )

            eval_result = self.evaluator.evaluate(
                task=task, result=result, contract=contract, iteration=iteration
            )
            eval_history.append(eval_result)

            if eval_result.score > best_score:
                best_score = eval_result.score

            self._fire(
                EvaluationCompleted(
                    run_id=run_id,
                    task_id=task.id,
                    contract_id=contract.contract_id,
                    iteration=iteration,
                    passed=eval_result.passed,
                    score=eval_result.score,
                )
            )

            if eval_result.passed:
                self._fire(
                    EvaluationConverged(
                        run_id=run_id,
                        task_id=task.id,
                        total_iterations=iteration,
                        final_score=eval_result.score,
                    )
                )
                return PipelineResult(
                    converged=True,
                    iterations=iteration,
                    eval_history=eval_history,
                    best_score=best_score,
                    final_eval=eval_result,
                )

        # Exhausted all iterations without convergence
        self._fire(
            EvaluationExhausted(
                run_id=run_id,
                task_id=task.id,
                max_iterations=self.max_iterations,
                best_score=best_score,
            )
        )

        return PipelineResult(
            converged=False,
            iterations=self.max_iterations,
            eval_history=eval_history,
            best_score=best_score,
            final_eval=eval_history[-1],
        )

    def _fire(self, event: Any) -> None:
        """Fire a lifecycle event through the hook registry if available."""
        if self._hooks is None:
            return
        try:
            self._hooks.fire(event)
        except Exception as exc:
            log.warning("pipeline.hook_fire_failed event=%s err=%s", type(event).__name__, exc)
