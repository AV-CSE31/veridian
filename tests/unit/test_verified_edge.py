"""
tests.unit.test_verified_edge
──────────────────────────────
Unit tests for VerifiedEdge and EdgeVerifier.
"""

from __future__ import annotations

from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.graph.verified_edge import EdgeVerifier, VerifiedEdge
from veridian.verify.base import BaseVerifier, VerificationResult

# ── Helpers ──────────────────────────────────────────────────────────────────


class _PassVerifier(BaseVerifier):
    id: ClassVar[str] = "_test_pass_verifier"
    description: ClassVar[str] = "Always passes"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class _FailVerifier(BaseVerifier):
    id: ClassVar[str] = "_test_fail_verifier"
    description: ClassVar[str] = "Always fails"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="deliberate failure")


def _make_task() -> Task:
    return Task(
        title="test task",
        description="test desc",
        verifier_id="_test_pass_verifier",
    )


def _make_result() -> TaskResult:
    return TaskResult(raw_output="test output")


# ── VerifiedEdge ─────────────────────────────────────────────────────────────


class TestVerifiedEdge:
    def test_creation_with_verifier_id(self) -> None:
        ve = VerifiedEdge(source="a", target="b", verifier_id="bash_exit")
        assert ve.source == "a"
        assert ve.target == "b"
        assert ve.verifier_id == "bash_exit"

    def test_creation_without_verifier_id(self) -> None:
        ve = VerifiedEdge(source="a", target="b")
        assert ve.verifier_id is None

    def test_is_graph_edge(self) -> None:
        """VerifiedEdge should be a GraphEdge subclass or have same interface."""
        ve = VerifiedEdge(source="a", target="b", verifier_id="v1")
        # Verify it has the same attributes as GraphEdge
        assert hasattr(ve, "source")
        assert hasattr(ve, "target")
        assert hasattr(ve, "condition")
        assert hasattr(ve, "verifier_id")


# ── EdgeVerifier ─────────────────────────────────────────────────────────────


class TestEdgeVerifier:
    def test_pass_through_no_verifier(self) -> None:
        """Edge without verifier_id always allows traversal."""
        edge = VerifiedEdge(source="a", target="b", verifier_id=None)
        registry: dict[str, BaseVerifier] = {}
        ev = EdgeVerifier(verifier_lookup=registry)
        assert ev.check_edge(edge, _make_task(), _make_result()) is True

    def test_blocks_when_verification_fails(self) -> None:
        """Edge with verifier_id blocks traversal when verification fails."""
        edge = VerifiedEdge(source="a", target="b", verifier_id="_test_fail_verifier")
        registry: dict[str, BaseVerifier] = {"_test_fail_verifier": _FailVerifier()}
        ev = EdgeVerifier(verifier_lookup=registry)
        assert ev.check_edge(edge, _make_task(), _make_result()) is False

    def test_allows_when_verification_passes(self) -> None:
        """Edge with verifier_id allows traversal when verification passes."""
        edge = VerifiedEdge(source="a", target="b", verifier_id="_test_pass_verifier")
        registry: dict[str, BaseVerifier] = {"_test_pass_verifier": _PassVerifier()}
        ev = EdgeVerifier(verifier_lookup=registry)
        assert ev.check_edge(edge, _make_task(), _make_result()) is True

    def test_multiple_edges_all_must_pass_for_join(self) -> None:
        """Multiple verified edges going to a join node: all must pass."""
        edges = [
            VerifiedEdge(source="a", target="join", verifier_id="_test_pass_verifier"),
            VerifiedEdge(source="b", target="join", verifier_id="_test_fail_verifier"),
        ]
        registry: dict[str, BaseVerifier] = {
            "_test_pass_verifier": _PassVerifier(),
            "_test_fail_verifier": _FailVerifier(),
        }
        ev = EdgeVerifier(verifier_lookup=registry)
        results = [ev.check_edge(e, _make_task(), _make_result()) for e in edges]
        # Not all pass -> join should not activate
        assert not all(results)

    def test_multiple_edges_all_pass(self) -> None:
        """Multiple verified edges, all pass -> join can activate."""
        edges = [
            VerifiedEdge(source="a", target="join", verifier_id="_test_pass_verifier"),
            VerifiedEdge(source="b", target="join", verifier_id="_test_pass_verifier"),
        ]
        registry: dict[str, BaseVerifier] = {"_test_pass_verifier": _PassVerifier()}
        ev = EdgeVerifier(verifier_lookup=registry)
        results = [ev.check_edge(e, _make_task(), _make_result()) for e in edges]
        assert all(results)

    def test_check_edge_returns_verification_result_detail(self) -> None:
        """check_edge_detail returns the full VerificationResult."""
        edge = VerifiedEdge(source="a", target="b", verifier_id="_test_fail_verifier")
        registry: dict[str, BaseVerifier] = {"_test_fail_verifier": _FailVerifier()}
        ev = EdgeVerifier(verifier_lookup=registry)
        vr = ev.check_edge_detail(edge, _make_task(), _make_result())
        assert vr is not None
        assert vr.passed is False
        assert vr.error == "deliberate failure"

    def test_check_edge_detail_no_verifier(self) -> None:
        """check_edge_detail returns None when no verifier_id set."""
        edge = VerifiedEdge(source="a", target="b", verifier_id=None)
        ev = EdgeVerifier(verifier_lookup={})
        vr = ev.check_edge_detail(edge, _make_task(), _make_result())
        assert vr is None
