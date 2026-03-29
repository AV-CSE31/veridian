"""
veridian.eval.sandbox
─────────────────────
EvolutionSandbox — orchestrates A/B comparison between agent versions.

Replays a task suite against two agent snapshots and produces an
UPGRADE / HOLD / ROLLBACK recommendation. Canary failures override
the comparison result with a hard ROLLBACK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veridian.eval.comparator import ComparisonResult, EvolutionComparator
from veridian.hooks.builtin.drift_detector import RunSnapshot

__all__ = ["EvolutionSandbox", "SandboxResult"]


@dataclass
class SandboxResult:
    """Result of an evolution sandbox comparison."""

    comparison: ComparisonResult | None = None
    recommendation: str = "hold"  # "upgrade" | "hold" | "rollback"
    canary_failures: list[str] = field(default_factory=list)
    canary_override: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "recommendation": self.recommendation,
            "canary_failures": self.canary_failures,
            "canary_override": self.canary_override,
            "reason": self.reason,
        }

    def to_markdown(self) -> str:
        """Generate sandbox result markdown."""
        lines = [
            "# Evolution Sandbox Result",
            "",
            f"**Recommendation:** {self.recommendation.upper()}",
            f"**Reason:** {self.reason}",
            "",
        ]

        if self.canary_override:
            lines.append("## Canary Override")
            lines.append("")
            lines.append("Canary regression detected. Evolution BLOCKED regardless of metrics.")
            lines.append("")
            for cid in self.canary_failures:
                lines.append(f"- {cid}")
            lines.append("")

        if self.comparison:
            lines.append(self.comparison.to_markdown())

        return "\n".join(lines)


class EvolutionSandbox:
    """Orchestrates A/B comparison between agent versions.

    Takes a task suite definition and two RunSnapshots.
    Optionally accepts canary failure list — hard gate on any regression.
    """

    def __init__(
        self,
        task_suite: list[dict[str, Any]],
        comparator: EvolutionComparator | None = None,
    ) -> None:
        self.task_suite = task_suite
        self._comparator = comparator or EvolutionComparator()

    def evaluate(
        self,
        snapshot_a: RunSnapshot,
        snapshot_b: RunSnapshot,
        canary_failures: list[str] | None = None,
    ) -> SandboxResult:
        """Compare two agent version snapshots.

        If canary_failures is non-empty, recommendation is forced to ROLLBACK.
        """
        comparison = self._comparator.compare(snapshot_a, snapshot_b)
        failures = canary_failures or []

        if failures:
            return SandboxResult(
                comparison=comparison,
                recommendation="rollback",
                canary_failures=failures,
                canary_override=True,
                reason=(
                    f"Canary regression: {len(failures)} previously passing canary "
                    f"task(s) now fail. Evolution blocked."
                ),
            )

        return SandboxResult(
            comparison=comparison,
            recommendation=comparison.recommendation,
            canary_failures=[],
            canary_override=False,
            reason=comparison.reason,
        )
