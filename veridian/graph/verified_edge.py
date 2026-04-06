"""
veridian.graph.verified_edge
──────────────────────────────
Edges with optional verification gates.

A VerifiedEdge extends GraphEdge with a ``verifier_id``. Before traversal,
the EdgeVerifier runs the referenced verifier; the edge is blocked if
verification fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from veridian.core.task import Task, TaskResult
from veridian.graph.state import GraphEdge
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = [
    "VerifiedEdge",
    "EdgeVerifier",
]

log = logging.getLogger(__name__)


@dataclass
class VerifiedEdge(GraphEdge):
    """A graph edge that optionally requires verification before traversal."""

    # Inherited from GraphEdge: source, target, condition, verifier_id, label
    # verifier_id is already on GraphEdge; this subclass adds semantic meaning


class EdgeVerifier:
    """
    Checks whether a verified edge allows traversal.

    Uses a verifier lookup dict (verifier_id -> BaseVerifier instance).
    """

    def __init__(self, verifier_lookup: dict[str, BaseVerifier]) -> None:
        self._lookup = verifier_lookup

    def check_edge(self, edge: GraphEdge, task: Task, result: TaskResult) -> bool:
        """
        Check whether the edge allows traversal.

        Returns True if:
        - The edge has no verifier_id (pass-through), OR
        - The referenced verifier returns passed=True.
        """
        if edge.verifier_id is None:
            return True

        verifier = self._lookup.get(edge.verifier_id)
        if verifier is None:
            log.warning(
                "edge_verifier.missing verifier_id=%s source=%s target=%s",
                edge.verifier_id,
                edge.source,
                edge.target,
            )
            return False

        vr = verifier.verify(task, result)
        log.debug(
            "edge_verifier.check source=%s target=%s verifier=%s passed=%s",
            edge.source,
            edge.target,
            edge.verifier_id,
            vr.passed,
        )
        return vr.passed

    def check_edge_detail(
        self, edge: GraphEdge, task: Task, result: TaskResult
    ) -> VerificationResult | None:
        """
        Return the full VerificationResult, or None if no verifier_id.
        """
        if edge.verifier_id is None:
            return None

        verifier = self._lookup.get(edge.verifier_id)
        if verifier is None:
            return VerificationResult(
                passed=False,
                error=f"Verifier '{edge.verifier_id}' not found",
            )

        return verifier.verify(task, result)
