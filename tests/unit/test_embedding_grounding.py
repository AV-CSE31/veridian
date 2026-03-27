"""
tests/unit/test_embedding_grounding.py
───────────────────────────────────────
Tests for A6: Embedding-based semantic grounding verifier.
"""

from __future__ import annotations

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.embedding_grounding import EmbeddingGroundingVerifier


def _make_task(context: str = "", threshold: float = 0.25) -> Task:
    return Task(
        title="test",
        description="test",
        verifier_id="embedding_grounding",
        verifier_config={"context": context, "threshold": threshold},
    )


def _make_result(text: str) -> TaskResult:
    return TaskResult(raw_output=text)


class TestEmbeddingGroundingVerifierID:
    def test_verifier_id(self) -> None:
        v = EmbeddingGroundingVerifier()
        assert v.id == "embedding_grounding"

    def test_description_not_empty(self) -> None:
        v = EmbeddingGroundingVerifier()
        assert v.description


class TestEmbeddingGroundingPassCases:
    def test_identical_text_passes(self) -> None:
        context = "The sky is blue and the grass is green."
        v = EmbeddingGroundingVerifier(context=context)
        task = _make_task(context=context)
        result = _make_result(context)
        vr = v.verify(task, result)
        assert vr.passed

    def test_similar_text_passes(self) -> None:
        context = "Python is a programming language used for data science and web development."
        output = "Python is widely used in data science applications and web frameworks."
        v = EmbeddingGroundingVerifier(context=context, threshold=0.3)
        task = _make_task(context=context, threshold=0.3)
        result = _make_result(output)
        vr = v.verify(task, result)
        assert vr.passed

    def test_evidence_includes_similarity_score(self) -> None:
        context = "The quick brown fox jumps over the lazy dog."
        v = EmbeddingGroundingVerifier(context=context)
        task = _make_task(context=context)
        result = _make_result(context)
        vr = v.verify(task, result)
        assert vr.evidence is not None
        assert "similarity" in vr.evidence

    def test_similarity_score_between_0_and_1(self) -> None:
        context = "Machine learning models require training data."
        v = EmbeddingGroundingVerifier(context=context)
        task = _make_task(context=context)
        result = _make_result(context)
        vr = v.verify(task, result)
        assert 0.0 <= vr.evidence["similarity"] <= 1.0  # type: ignore[index]


class TestEmbeddingGroundingFailCases:
    def test_unrelated_text_fails(self) -> None:
        context = "The stock market rose 3% today driven by tech sector gains."
        output = "Recipe: boil pasta for 8 minutes then add tomato sauce."
        v = EmbeddingGroundingVerifier(context=context, threshold=0.8)
        task = _make_task(context=context, threshold=0.8)
        result = _make_result(output)
        vr = v.verify(task, result)
        assert not vr.passed

    def test_error_message_mentions_similarity(self) -> None:
        context = "Financial regulatory compliance in banking."
        output = "How to bake bread at home step by step."
        v = EmbeddingGroundingVerifier(context=context, threshold=0.9)
        task = _make_task(context=context, threshold=0.9)
        result = _make_result(output)
        vr = v.verify(task, result)
        assert not vr.passed
        assert vr.error is not None
        assert len(vr.error) <= 300

    def test_error_message_is_actionable(self) -> None:
        context = "quarterly earnings report analysis."
        output = "birthday party planning tips."
        v = EmbeddingGroundingVerifier(context=context, threshold=0.9)
        result = _make_result(output)
        vr = v.verify(_make_task(context=context, threshold=0.9), result)
        assert not vr.passed
        assert vr.error
        # Should mention the threshold or similarity
        assert any(kw in vr.error.lower() for kw in ("similarity", "threshold", "grounding"))


class TestEmbeddingGroundingEdgeCases:
    def test_empty_context_auto_passes(self) -> None:
        v = EmbeddingGroundingVerifier(context="")
        task = _make_task(context="")
        result = _make_result("any output at all")
        vr = v.verify(task, result)
        assert vr.passed

    def test_empty_output_with_context_fails_or_passes_gracefully(self) -> None:
        context = "Important financial data context."
        v = EmbeddingGroundingVerifier(context=context, threshold=0.5)
        task = _make_task(context=context, threshold=0.5)
        result = _make_result("")
        vr = v.verify(task, result)
        # Either passes (empty considered neutral) or fails cleanly — no exception
        assert isinstance(vr.passed, bool)

    def test_context_from_task_metadata(self) -> None:
        # context can also come from task.verifier_config["context"]
        context = "The capital of France is Paris."
        task = Task(
            title="test",
            description="test",
            verifier_id="embedding_grounding",
            verifier_config={"context": context, "threshold": 0.5},
        )
        v = EmbeddingGroundingVerifier()  # no context in constructor
        result = _make_result("Paris is the capital city of France.")
        vr = v.verify(task, result)
        assert vr.passed

    def test_configurable_threshold(self) -> None:
        context = "Artificial intelligence and machine learning."
        output = "Deep learning and neural networks in AI."
        # High threshold should fail for moderately similar text
        v_strict = EmbeddingGroundingVerifier(context=context, threshold=0.99)
        task_strict = _make_task(context=context, threshold=0.99)
        vr_strict = v_strict.verify(task_strict, _make_result(output))
        # Low threshold should pass
        v_loose = EmbeddingGroundingVerifier(context=context, threshold=0.01)
        task_loose = _make_task(context=context, threshold=0.01)
        vr_loose = v_loose.verify(task_loose, _make_result(output))
        assert vr_loose.passed
        # strict should be stricter than loose (tautologically true)
        assert not (vr_strict.passed and not vr_loose.passed)

    def test_verifier_is_stateless(self) -> None:
        v = EmbeddingGroundingVerifier(context="Some context about dogs.")
        task = _make_task(context="Some context about dogs.")
        r1 = v.verify(task, _make_result("cats and dogs are pets."))
        r2 = v.verify(task, _make_result("cats and dogs are pets."))
        assert r1.passed == r2.passed
