"""
tests/unit/test_adversarial_eval.py
────────────────────────────────────
Unit tests for the Adversarial Evaluator Pipeline.

Covers:
  - SprintContract creation, signing, and validation
  - CalibrationProfile / GradingRubric weight validation
  - AdversarialEvaluator: pass/fail/error paths + feedback quality
  - VerificationPipeline: convergence, exhaustion, iteration tracking
  - Exception hierarchy correctness
  - Hook event firing (ContractNegotiated, EvaluationStarted, etc.)
"""

from __future__ import annotations

import json

import pytest

from veridian.core.exceptions import (
    CalibrationError,
    ContractViolation,
    EvaluationError,
)
from veridian.core.task import Task, TaskResult
from veridian.eval.adversarial import AdversarialEvaluator, EvaluationResult
from veridian.eval.calibration import CalibrationProfile, GradingRubric, RubricCriterion
from veridian.eval.pipeline import VerificationPipeline
from veridian.eval.sprint_contract import SprintContract
from veridian.providers.mock_provider import MockProvider

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def basic_task() -> Task:
    return Task(
        id="t1",
        title="Write a sorting algorithm",
        description="Implement merge sort. Output: working Python code.",
        verifier_id="schema",
    )


@pytest.fixture
def basic_result() -> TaskResult:
    return TaskResult(
        raw_output="<veridian:result>{"
        '"summary": "Implemented merge sort",'
        '"structured": {"code": "def merge_sort(arr): return arr"},'
        '"artifacts": []'
        "}</veridian:result>",
        structured={"code": "def merge_sort(arr): return arr"},
    )


@pytest.fixture
def rubric() -> GradingRubric:
    return GradingRubric(
        name="code_quality",
        criteria=[
            RubricCriterion(name="correctness", description="Code is correct", weight=0.5),
            RubricCriterion(name="readability", description="Code is readable", weight=0.3),
            RubricCriterion(name="efficiency", description="Code is efficient", weight=0.2),
        ],
    )


@pytest.fixture
def calibration(rubric: GradingRubric) -> CalibrationProfile:
    return CalibrationProfile(
        skepticism=0.5,
        rubric=rubric,
        pass_threshold=0.7,
    )


@pytest.fixture
def contract(basic_task: Task) -> SprintContract:
    """Unsigned contract — for tests that explicitly test signing behaviour."""
    return SprintContract(
        task_id=basic_task.id,
        deliverables=["Working Python merge sort implementation"],
        success_criteria=["All test cases pass", "O(n log n) complexity"],
        test_conditions=["Input: [3,1,2] → Output: [1,2,3]"],
        evaluation_threshold=0.7,
    )


@pytest.fixture
def signed_contract(basic_task: Task) -> SprintContract:
    """Fully signed contract — for tests that exercise pipeline execution."""
    c = SprintContract(
        task_id=basic_task.id,
        deliverables=["Working Python merge sort implementation"],
        success_criteria=["All test cases pass", "O(n log n) complexity"],
        test_conditions=["Input: [3,1,2] → Output: [1,2,3]"],
        evaluation_threshold=0.7,
    )
    c.sign_generator()
    c.sign_evaluator()
    return c


@pytest.fixture
def passing_eval_response() -> str:
    """Mock LLM response that evaluates positively."""
    payload = json.dumps(
        {
            "passed": True,
            "score": 0.85,
            "criterion_scores": {
                "correctness": 0.9,
                "readability": 0.8,
                "efficiency": 0.8,
            },
            "failures": [],
            "feedback": "Well-implemented merge sort with clear variable naming.",
        }
    )
    return f"<veridian:eval>{payload}</veridian:eval>"


@pytest.fixture
def failing_eval_response() -> str:
    """Mock LLM response that evaluates negatively with specific citations."""
    payload = json.dumps(
        {
            "passed": False,
            "score": 0.45,
            "criterion_scores": {
                "correctness": 0.3,
                "readability": 0.7,
                "efficiency": 0.2,
            },
            "failures": [
                "correctness: Implementation returns input unchanged, does not sort",
                "efficiency: No divide-and-conquer logic present",
            ],
            "feedback": (
                "The merge sort implementation is incomplete. "
                "The function body `return arr` does not sort. "
                "Add recursive splitting and merge steps."
            ),
        }
    )
    return f"<veridian:eval>{payload}</veridian:eval>"


# ─────────────────────────────────────────────────────────────────────────────
# SprintContract tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSprintContract:
    def test_creates_with_unique_id(self, basic_task: Task) -> None:
        """Should auto-generate a contract_id if not provided."""
        c1 = SprintContract(
            task_id=basic_task.id,
            deliverables=["x"],
            success_criteria=["y"],
            test_conditions=[],
            evaluation_threshold=0.8,
        )
        c2 = SprintContract(
            task_id=basic_task.id,
            deliverables=["x"],
            success_criteria=["y"],
            test_conditions=[],
            evaluation_threshold=0.8,
        )
        assert c1.contract_id != c2.contract_id

    def test_not_signed_by_default(self, contract: SprintContract) -> None:
        """Should start unsigned on both sides."""
        assert not contract.signed_by_generator
        assert not contract.signed_by_evaluator
        assert not contract.is_signed()

    def test_sign_generator(self, contract: SprintContract) -> None:
        """Should mark generator as signed."""
        contract.sign_generator()
        assert contract.signed_by_generator
        assert not contract.is_signed()  # still needs evaluator

    def test_sign_evaluator(self, contract: SprintContract) -> None:
        """Should mark evaluator as signed."""
        contract.sign_evaluator()
        assert contract.signed_by_evaluator
        assert not contract.is_signed()  # still needs generator

    def test_fully_signed_when_both_signed(self, contract: SprintContract) -> None:
        """Should report is_signed() True only after both parties sign."""
        contract.sign_generator()
        contract.sign_evaluator()
        assert contract.is_signed()

    def test_raises_on_invalid_threshold(self) -> None:
        """Should raise ContractViolation when threshold is out of range."""
        with pytest.raises(ContractViolation, match="evaluation_threshold"):
            SprintContract(
                task_id="t1",
                deliverables=["x"],
                success_criteria=["y"],
                test_conditions=[],
                evaluation_threshold=1.5,  # invalid
            )

    def test_raises_on_empty_deliverables(self) -> None:
        """Should raise ContractViolation when deliverables list is empty."""
        with pytest.raises(ContractViolation, match="deliverables"):
            SprintContract(
                task_id="t1",
                deliverables=[],
                success_criteria=["y"],
                test_conditions=[],
                evaluation_threshold=0.7,
            )

    def test_to_dict_round_trips(self, contract: SprintContract) -> None:
        """Should serialize to dict and back preserving all fields."""
        d = contract.to_dict()
        c2 = SprintContract.from_dict(d)
        assert c2.contract_id == contract.contract_id
        assert c2.task_id == contract.task_id
        assert c2.deliverables == contract.deliverables
        assert c2.evaluation_threshold == contract.evaluation_threshold


# ─────────────────────────────────────────────────────────────────────────────
# CalibrationProfile / GradingRubric tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCalibrationProfile:
    def test_valid_weights_accepted(self, calibration: CalibrationProfile) -> None:
        """Should accept rubric where weights sum to 1.0."""
        assert calibration.rubric.validate_weights()

    def test_invalid_weights_raise(self) -> None:
        """Should raise CalibrationError when criterion weights don't sum to 1.0."""
        bad_rubric = GradingRubric(
            name="bad",
            criteria=[
                RubricCriterion(name="a", description="", weight=0.6),
                RubricCriterion(name="b", description="", weight=0.6),  # sums to 1.2
            ],
        )
        with pytest.raises(CalibrationError, match="weights"):
            CalibrationProfile(skepticism=0.5, rubric=bad_rubric, pass_threshold=0.7)

    def test_skepticism_out_of_range_raises(self, rubric: GradingRubric) -> None:
        """Should raise CalibrationError when skepticism is outside [0.0, 1.0]."""
        with pytest.raises(CalibrationError, match="skepticism"):
            CalibrationProfile(skepticism=1.5, rubric=rubric, pass_threshold=0.7)

    def test_pass_threshold_out_of_range_raises(self, rubric: GradingRubric) -> None:
        """Should raise CalibrationError when pass_threshold is outside (0.0, 1.0]."""
        with pytest.raises(CalibrationError, match="pass_threshold"):
            CalibrationProfile(skepticism=0.5, rubric=rubric, pass_threshold=0.0)

    def test_compute_weighted_score(self, calibration: CalibrationProfile) -> None:
        """Should compute weighted aggregate from criterion scores."""
        criterion_scores = {"correctness": 0.8, "readability": 1.0, "efficiency": 0.6}
        score = calibration.compute_weighted_score(criterion_scores)
        # 0.8*0.5 + 1.0*0.3 + 0.6*0.2 = 0.40 + 0.30 + 0.12 = 0.82
        assert abs(score - 0.82) < 1e-9

    def test_missing_criterion_scores_raises(self, calibration: CalibrationProfile) -> None:
        """Should raise CalibrationError when a criterion score is missing."""
        with pytest.raises(CalibrationError, match="criterion"):
            calibration.compute_weighted_score({"correctness": 0.8})

    def test_default_profile_is_balanced(self) -> None:
        """CalibrationProfile.default() should have equal weights and skepticism=0.5."""
        profile = CalibrationProfile.default()
        assert profile.skepticism == 0.5
        assert profile.pass_threshold == 0.7
        assert profile.rubric.validate_weights()


# ─────────────────────────────────────────────────────────────────────────────
# AdversarialEvaluator tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAdversarialEvaluator:
    @pytest.fixture
    def evaluator(self, calibration: CalibrationProfile) -> AdversarialEvaluator:
        return AdversarialEvaluator(
            provider=MockProvider(),
            calibration=calibration,
        )

    def test_passes_when_llm_says_pass(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """Should return EvaluationResult(passed=True) when LLM scores above threshold."""
        evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        result = evaluator.evaluate(task=basic_task, result=basic_result, contract=contract)
        assert result.passed is True
        assert result.score >= 0.7
        assert result.failures == []

    def test_fails_with_specific_citations(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        failing_eval_response: str,
    ) -> None:
        """Should return EvaluationResult(passed=False) with failure citations."""
        evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        result = evaluator.evaluate(task=basic_task, result=basic_result, contract=contract)
        assert result.passed is False
        assert result.score < 0.7
        assert len(result.failures) >= 1
        # At least one failure must name a criterion
        assert any("correctness" in f for f in result.failures)

    def test_feedback_is_actionable(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        failing_eval_response: str,
    ) -> None:
        """Feedback must be non-empty and ≤ 2000 chars (agent context budget)."""
        evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        result = evaluator.evaluate(task=basic_task, result=basic_result, contract=contract)
        assert result.feedback
        assert len(result.feedback) <= 2000

    def test_malformed_llm_response_raises_evaluation_error(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
    ) -> None:
        """Should raise EvaluationError when LLM returns unparseable output."""
        evaluator._provider.script_text("this is not valid eval output")  # type: ignore[attr-defined]
        with pytest.raises(EvaluationError, match="parse"):
            evaluator.evaluate(task=basic_task, result=basic_result, contract=contract)

    def test_evaluator_is_structurally_separate_from_generator(
        self,
        calibration: CalibrationProfile,
    ) -> None:
        """Generator and evaluator must use independent provider instances."""
        gen_provider = MockProvider()
        eval_provider = MockProvider()
        evaluator = AdversarialEvaluator(provider=eval_provider, calibration=calibration)
        # The evaluator only uses its own provider, never the generator's
        assert evaluator._provider is eval_provider
        assert evaluator._provider is not gen_provider

    def test_criterion_scores_match_rubric(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """criterion_scores must contain exactly the rubric's criterion names."""
        evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        result = evaluator.evaluate(task=basic_task, result=basic_result, contract=contract)
        rubric_names = {c.name for c in evaluator.calibration.rubric.criteria}
        assert set(result.criterion_scores.keys()) == rubric_names

    def test_iteration_counter_is_set(
        self,
        evaluator: AdversarialEvaluator,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """EvaluationResult.iteration must equal the passed iteration number."""
        evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        result = evaluator.evaluate(
            task=basic_task, result=basic_result, contract=contract, iteration=3
        )
        assert result.iteration == 3


# ─────────────────────────────────────────────────────────────────────────────
# VerificationPipeline tests
# ─────────────────────────────────────────────────────────────────────────────


class TestVerificationPipeline:
    @pytest.fixture
    def pipeline(self, calibration: CalibrationProfile) -> VerificationPipeline:
        return VerificationPipeline(
            evaluator=AdversarialEvaluator(
                provider=MockProvider(),
                calibration=calibration,
            ),
            max_iterations=3,
        )

    def test_converges_on_first_pass(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """Should return PipelineResult(converged=True) on first passing eval."""
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        assert pr.converged is True
        assert pr.iterations == 1

    def test_exhausts_after_max_iterations(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        failing_eval_response: str,
    ) -> None:
        """Should set converged=False after max_iterations failing evals."""
        # Script 3 failing responses (max_iterations=3)
        for _ in range(3):
            pipeline.evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        assert pr.converged is False
        assert pr.iterations == 3

    def test_converges_on_second_attempt(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        failing_eval_response: str,
        passing_eval_response: str,
    ) -> None:
        """Should converge after 2 iterations (fail then pass)."""
        pipeline.evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        assert pr.converged is True
        assert pr.iterations == 2

    def test_pipeline_result_contains_all_eval_results(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        failing_eval_response: str,
        passing_eval_response: str,
    ) -> None:
        """PipelineResult.eval_history should contain one entry per iteration."""
        pipeline.evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        assert len(pr.eval_history) == 2
        assert pr.eval_history[0].passed is False
        assert pr.eval_history[1].passed is True

    def test_best_score_tracked(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        failing_eval_response: str,
    ) -> None:
        """PipelineResult.best_score should be the highest score across all iterations."""
        for _ in range(3):
            pipeline.evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        # Weighted score: 0.3*0.5 + 0.7*0.3 + 0.2*0.2 = 0.15 + 0.21 + 0.04 = 0.40
        # (pipeline recomputes from rubric weights, not LLM-reported value)
        assert abs(pr.best_score - 0.40) < 1e-9

    def test_final_eval_result_is_last_iteration(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        signed_contract: SprintContract,
        failing_eval_response: str,
        passing_eval_response: str,
    ) -> None:
        """PipelineResult.final_eval should be the last EvaluationResult."""
        pipeline.evaluator._provider.script_text(failing_eval_response)  # type: ignore[attr-defined]
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=signed_contract)
        assert pr.final_eval.passed is True

    def test_requires_signed_contract(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """Pipeline must enforce that contract is signed before running."""
        # Contract is unsigned by default — sign only generator
        contract.sign_generator()
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        with pytest.raises(ContractViolation, match="not fully signed"):
            pipeline.run(task=basic_task, result=basic_result, contract=contract)

    def test_unsigned_contract_blocked_entirely(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """Fully unsigned contract must also be rejected."""
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        with pytest.raises(ContractViolation, match="not fully signed"):
            pipeline.run(task=basic_task, result=basic_result, contract=contract)

    def test_signed_contract_runs_successfully(
        self,
        pipeline: VerificationPipeline,
        basic_task: Task,
        basic_result: TaskResult,
        contract: SprintContract,
        passing_eval_response: str,
    ) -> None:
        """Fully signed contract should allow pipeline to run."""
        contract.sign_generator()
        contract.sign_evaluator()
        pipeline.evaluator._provider.script_text(passing_eval_response)  # type: ignore[attr-defined]
        pr = pipeline.run(task=basic_task, result=basic_result, contract=contract)
        assert pr.converged is True


# ─────────────────────────────────────────────────────────────────────────────
# Exception hierarchy tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExceptionHierarchy:
    def test_evaluation_error_is_veridian_error(self) -> None:
        from veridian.core.exceptions import VeridianError

        err = EvaluationError("test")
        assert isinstance(err, VeridianError)

    def test_contract_violation_captures_fields(self) -> None:
        err = ContractViolation(contract_id="c1", reason="deliverables empty")
        assert err.contract_id == "c1"
        assert err.reason == "deliverables empty"
        assert "c1" in str(err)

    def test_calibration_error_is_veridian_error(self) -> None:
        from veridian.core.exceptions import VeridianError

        err = CalibrationError("weights don't sum to 1.0")
        assert isinstance(err, VeridianError)


# ─────────────────────────────────────────────────────────────────────────────
# EvaluationResult tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluationResult:
    def test_passed_result_has_no_failures(self) -> None:
        """A passing EvaluationResult should have empty failures list."""
        r = EvaluationResult(
            passed=True,
            score=0.9,
            criterion_scores={"correctness": 0.9},
            failures=[],
            feedback="Excellent work.",
            iteration=1,
        )
        assert r.passed
        assert r.failures == []

    def test_failed_result_has_failure_citations(self) -> None:
        """A failing EvaluationResult must contain at least one failure citation."""
        r = EvaluationResult(
            passed=False,
            score=0.3,
            criterion_scores={"correctness": 0.3},
            failures=["correctness: implementation missing"],
            feedback="Fix the implementation.",
            iteration=1,
        )
        assert not r.passed
        assert len(r.failures) >= 1

    def test_to_dict_is_json_serialisable(self) -> None:
        r = EvaluationResult(
            passed=True,
            score=0.85,
            criterion_scores={"a": 0.85},
            failures=[],
            feedback="Good.",
            iteration=2,
        )
        d = r.to_dict()
        # Must be JSON-serializable
        json.dumps(d)
        assert d["passed"] is True
        assert d["score"] == 0.85
        assert d["iteration"] == 2
