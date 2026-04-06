"""
tests.unit.test_explain
────────────────────────
Verification Explanation Engine — human-readable explanations for every
verification decision, with detail levels and structured evidence links.
"""

from __future__ import annotations

from veridian.core.task import Task, TaskResult
from veridian.explain.engine import (
    Evidence,
    EvidenceType,
    Explanation,
    ExplanationDetail,
    ExplanationEngine,
)
from veridian.verify.base import VerificationResult

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_task(task_id: str = "t1") -> Task:
    return Task(id=task_id, title="Test Task", description="A test task", verifier_id="schema")


def _make_result(task_id: str = "t1") -> TaskResult:
    return TaskResult(raw_output="done", structured={"answer": "42"})


def _pass_result(**kwargs) -> VerificationResult:
    return VerificationResult(passed=True, **kwargs)


def _fail_result(
    error: str = "Schema mismatch: missing field 'answer'", **kwargs
) -> VerificationResult:
    return VerificationResult(passed=False, error=error, **kwargs)


# ── Evidence ──────────────────────────────────────────────────────────────────


class TestEvidence:
    def test_construct(self) -> None:
        ev = Evidence(
            type=EvidenceType.FIELD_VALUE,
            content="field 'answer' was '42'",
            location="output.answer",
        )
        assert ev.type == EvidenceType.FIELD_VALUE
        assert ev.location == "output.answer"

    def test_serialise_round_trip(self) -> None:
        ev = Evidence(
            type=EvidenceType.PATTERN_MATCH,
            content="matched regex r'^\\d+$'",
            location="output.code",
        )
        d = ev.to_dict()
        ev2 = Evidence.from_dict(d)
        assert ev2.type == ev.type
        assert ev2.content == ev.content
        assert ev2.location == ev.location


# ── Explanation ───────────────────────────────────────────────────────────────


class TestExplanation:
    def test_construct_pass(self) -> None:
        exp = Explanation(
            verifier_id="schema",
            task_id="t1",
            passed=True,
            reason="All required fields present and correctly typed.",
            detail_level=ExplanationDetail.STANDARD,
            evidence=[],
        )
        assert exp.passed is True
        assert "All required fields" in exp.reason

    def test_construct_fail(self) -> None:
        exp = Explanation(
            verifier_id="schema",
            task_id="t1",
            passed=False,
            reason="Missing required field: 'answer'",
            detail_level=ExplanationDetail.BRIEF,
            evidence=[Evidence(EvidenceType.MISSING_FIELD, "field 'answer'", "output")],
        )
        assert exp.passed is False
        assert len(exp.evidence) == 1

    def test_summary_brief(self) -> None:
        exp = Explanation(
            verifier_id="schema",
            task_id="t1",
            passed=False,
            reason="Missing required field: 'answer'",
            detail_level=ExplanationDetail.BRIEF,
            evidence=[],
        )
        summary = exp.summary()
        assert "FAILED" in summary or "failed" in summary.lower()
        assert len(summary) < 300  # brief should be concise

    def test_summary_detailed_includes_evidence(self) -> None:
        ev = Evidence(EvidenceType.FIELD_VALUE, "field was null", "output.x")
        exp = Explanation(
            verifier_id="schema",
            task_id="t1",
            passed=False,
            reason="Field was null",
            detail_level=ExplanationDetail.DETAILED,
            evidence=[ev],
        )
        summary = exp.summary()
        assert "null" in summary or "evidence" in summary.lower() or "output.x" in summary

    def test_serialise_round_trip(self) -> None:
        ev = Evidence(EvidenceType.FIELD_VALUE, "val='42'", "output.answer")
        exp = Explanation(
            verifier_id="schema",
            task_id="t1",
            passed=True,
            reason="All checks passed.",
            detail_level=ExplanationDetail.STANDARD,
            evidence=[ev],
        )
        d = exp.to_dict()
        exp2 = Explanation.from_dict(d)
        assert exp2.verifier_id == exp.verifier_id
        assert exp2.passed == exp.passed
        assert len(exp2.evidence) == 1


# ── ExplanationEngine ─────────────────────────────────────────────────────────


class TestExplanationEngine:
    def test_explain_pass_brief(self) -> None:
        engine = ExplanationEngine()
        result = _pass_result(score=1.0)
        task = _make_task()
        tr = _make_result()
        exp = engine.explain(
            result=result,
            task=task,
            task_result=tr,
            verifier_id="schema",
            detail=ExplanationDetail.BRIEF,
        )
        assert isinstance(exp, Explanation)
        assert exp.passed is True
        assert exp.verifier_id == "schema"
        assert exp.task_id == "t1"

    def test_explain_fail_standard(self) -> None:
        engine = ExplanationEngine()
        result = _fail_result(error="Schema mismatch: missing 'answer'")
        task = _make_task()
        tr = _make_result()
        exp = engine.explain(
            result=result,
            task=task,
            task_result=tr,
            verifier_id="schema",
            detail=ExplanationDetail.STANDARD,
        )
        assert exp.passed is False
        assert "answer" in exp.reason or "Schema mismatch" in exp.reason

    def test_explain_fail_detailed_has_evidence(self) -> None:
        engine = ExplanationEngine()
        result = _fail_result(
            error="Schema mismatch: field 'answer' required",
            evidence={"missing_fields": ["answer"], "provided_fields": ["result"]},
        )
        task = _make_task()
        tr = _make_result()
        exp = engine.explain(
            result=result,
            task=task,
            task_result=tr,
            verifier_id="schema",
            detail=ExplanationDetail.DETAILED,
        )
        assert exp.passed is False
        # Detailed level should include evidence
        assert exp.evidence is not None

    def test_explain_with_score(self) -> None:
        engine = ExplanationEngine()
        result = VerificationResult(passed=True, score=0.87)
        task = _make_task()
        tr = _make_result()
        exp = engine.explain(
            result=result,
            task=task,
            task_result=tr,
            verifier_id="llm_judge",
            detail=ExplanationDetail.STANDARD,
        )
        assert exp.passed is True
        # Score should appear in reason or summary at standard+ level
        summary = exp.summary()
        assert "0.87" in summary or "score" in summary.lower()

    def test_explain_returns_explanation_type(self) -> None:
        engine = ExplanationEngine()
        result = _pass_result()
        exp = engine.explain(
            result=result,
            task=_make_task(),
            task_result=_make_result(),
            verifier_id="bash_exit",
            detail=ExplanationDetail.BRIEF,
        )
        assert isinstance(exp, Explanation)

    def test_explain_fail_error_appears_in_reason(self) -> None:
        engine = ExplanationEngine()
        error_msg = "HTTP status 500 — server error"
        result = VerificationResult(passed=False, error=error_msg)
        exp = engine.explain(
            result=result,
            task=_make_task(),
            task_result=_make_result(),
            verifier_id="http_status",
            detail=ExplanationDetail.STANDARD,
        )
        assert error_msg in exp.reason or "500" in exp.reason

    def test_explain_all_detail_levels(self) -> None:
        engine = ExplanationEngine()
        result = _fail_result(error="test failure")
        task = _make_task()
        tr = _make_result()
        for level in ExplanationDetail:
            exp = engine.explain(
                result=result,
                task=task,
                task_result=tr,
                verifier_id="schema",
                detail=level,
            )
            assert isinstance(exp, Explanation)
            assert exp.detail_level == level

    def test_batch_explain(self) -> None:
        engine = ExplanationEngine()
        items = [
            (
                VerificationResult(passed=i % 2 == 0, error="err" if i % 2 != 0 else None),
                _make_task(f"t{i}"),
                TaskResult(raw_output="done", structured={}),
                "schema",
            )
            for i in range(4)
        ]
        explanations = engine.batch_explain(items, detail=ExplanationDetail.BRIEF)
        assert len(explanations) == 4
        for exp in explanations:
            assert isinstance(exp, Explanation)
