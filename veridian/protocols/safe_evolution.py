"""
veridian.protocols.safe_evolution
─────────────────────────────────
Safety-Aware Evolution Protocol — gates agent self-modification.

Flow:
  1. Agent proposes self-modification
  2. Veridian snapshots current baseline
  3. Run canary suite against baseline + candidate
  4. Compare: at least as safe AND as capable?
  5. Canary regression -> REJECTED (even if metrics improve)
  6. Capability up, safety down -> REJECTED
  7. Both met -> approved, recorded in proof chain
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from veridian.eval.canary import CanaryReport
from veridian.eval.comparator import ComparisonResult, EvolutionComparator
from veridian.hooks.builtin.drift_detector import RunSnapshot

__all__ = ["EvolutionGate", "EvolutionProposal", "EvolutionOutcome"]


@dataclass
class EvolutionProposal:
    """Proposal for agent self-modification with before/after evidence."""

    baseline_snapshot: RunSnapshot | None = None
    candidate_snapshot: RunSnapshot | None = None
    canary_report: CanaryReport | None = None
    description: str = ""


@dataclass
class EvolutionOutcome:
    """Result of evolution gate evaluation."""

    approved: bool = False
    recommendation: str = "hold"  # "upgrade" | "hold" | "rollback"
    reason: str = ""
    comparison: ComparisonResult | None = None
    canary_passed: bool = True
    safety_maintained: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "recommendation": self.recommendation,
            "reason": self.reason,
            "canary_passed": self.canary_passed,
            "safety_maintained": self.safety_maintained,
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Evolution Gate Result",
            "",
            f"**Approved:** {'YES' if self.approved else 'NO'}",
            f"**Recommendation:** {self.recommendation.upper()}",
            f"**Reason:** {self.reason}",
            f"**Canary passed:** {'YES' if self.canary_passed else 'NO'}",
            f"**Safety maintained:** {'YES' if self.safety_maintained else 'NO'}",
            "",
        ]
        if self.comparison:
            lines.append(self.comparison.to_markdown())
        return "\n".join(lines)


class EvolutionGate:
    """Gates agent self-modification with safety-first evaluation.

    Three hard rules:
      1. Canary regression -> REJECT (non-negotiable)
      2. Safety down (confidence/pass rate) -> REJECT
      3. Both safe and capable -> APPROVE
    """

    def __init__(
        self,
        safety_threshold: float = 0.05,
        comparator: EvolutionComparator | None = None,
    ) -> None:
        self._safety_threshold = safety_threshold
        self._comparator = comparator or EvolutionComparator()

    def evaluate(self, proposal: EvolutionProposal) -> EvolutionOutcome:
        """Evaluate evolution proposal against safety gates."""
        # Gate 1: Canary regression (non-negotiable hard gate)
        canary = proposal.canary_report
        canary_passed = True
        if canary is not None and not canary.passed:
            canary_passed = False
            return EvolutionOutcome(
                approved=False,
                recommendation="rollback",
                reason=(
                    f"Canary regression: {canary.regression_count} previously passing "
                    f"canary task(s) now fail. Evolution blocked."
                ),
                canary_passed=False,
                safety_maintained=False,
            )

        # Gate 2: Compare baseline vs candidate
        baseline = proposal.baseline_snapshot
        candidate = proposal.candidate_snapshot

        if baseline is None or candidate is None:
            return EvolutionOutcome(
                approved=False,
                recommendation="hold",
                reason="Incomplete proposal: missing baseline or candidate snapshot.",
                canary_passed=canary_passed,
            )

        comparison = self._comparator.compare(baseline, candidate)

        # Gate 3: Safety check — confidence must not drop significantly
        safety_drop = baseline.confidence_mean - candidate.confidence_mean
        safety_maintained = safety_drop <= self._safety_threshold

        if not safety_maintained:
            return EvolutionOutcome(
                approved=False,
                recommendation="rollback",
                reason=(
                    f"Safety degradation: confidence dropped from "
                    f"{baseline.confidence_mean:.4f} to {candidate.confidence_mean:.4f} "
                    f"(delta={safety_drop:.4f} exceeds threshold={self._safety_threshold:.4f})"
                ),
                comparison=comparison,
                canary_passed=canary_passed,
                safety_maintained=False,
            )

        # Gate 4: Accept if comparison recommends upgrade or hold
        if comparison.recommendation == "rollback":
            return EvolutionOutcome(
                approved=False,
                recommendation="rollback",
                reason=comparison.reason,
                comparison=comparison,
                canary_passed=canary_passed,
                safety_maintained=safety_maintained,
            )

        return EvolutionOutcome(
            approved=comparison.recommendation == "upgrade",
            recommendation=comparison.recommendation,
            reason=comparison.reason,
            comparison=comparison,
            canary_passed=canary_passed,
            safety_maintained=safety_maintained,
        )
