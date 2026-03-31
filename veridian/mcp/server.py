"""
veridian.mcp.server
────────────────────
MCP-compatible Skill Server — exposes verified procedures to
Claude Code, Cursor, Windsurf, and other MCP-capable tools.

Features:
  - Skill requests filtered by minimum bayesian_lower_bound
  - Imported skills go through quarantine automatically
  - Full provenance chain attached to every shared skill
  - MCP tool definition format for autodiscovery
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from veridian.skills.models import Skill
from veridian.skills.quarantine import QuarantineResult, QuarantineStatus, SkillQuarantine

__all__ = [
    "MCPSkillServer",
    "SkillRequest",
    "SkillResponse",
    "ToolDefinition",
    "ImportResult",
]

log = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """MCP tool definition for a verified skill."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    skill_id: str = ""
    reliability: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "skill_id": self.skill_id,
            "reliability": round(self.reliability, 4),
        }


@dataclass
class SkillRequest:
    """Request to execute a verified skill."""

    skill_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResponse:
    """Response from skill execution."""

    skill_id: str = ""
    steps: list[dict[str, Any]] | None = None
    provenance: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "steps": self.steps,
            "provenance": self.provenance,
            "error": self.error,
        }


@dataclass
class ImportResult:
    """Result of importing an external skill."""

    accepted: bool = False
    skill_id: str = ""
    violations: list[str] = field(default_factory=list)
    quarantine_result: QuarantineResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "skill_id": self.skill_id,
            "violations": self.violations,
        }


def _bayesian_lower_bound(alpha: float, beta_: float) -> float:
    """Bayesian Beta lower bound (95% CI)."""
    n = alpha + beta_
    if n == 0:
        return 0.0
    p = alpha / n
    variance = (p * (1.0 - p)) / n
    return max(0.0, p - 1.96 * math.sqrt(variance))


class MCPSkillServer:
    """MCP-compatible skill server exposing verified procedures.

    Skills are filtered by minimum Bayesian reliability lower bound.
    Imported skills go through SkillQuarantine automatically.
    """

    def __init__(
        self,
        skills: list[Skill],
        min_reliability: float = 0.50,
        quarantine: SkillQuarantine | None = None,
    ) -> None:
        self._skills = {s.id: s for s in skills}
        self._min_reliability = min_reliability
        self._quarantine = quarantine or SkillQuarantine()

    def list_tools(self) -> list[ToolDefinition]:
        """Return MCP tool definitions for all qualifying skills."""
        tools: list[ToolDefinition] = []
        for skill in self._skills.values():
            reliability = _bayesian_lower_bound(skill.alpha, skill.beta_)
            if reliability < self._min_reliability:
                continue
            tools.append(
                ToolDefinition(
                    name=f"veridian_skill_{skill.id}",
                    description=f"{skill.name}: {skill.trigger}",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "input": {"type": "string", "description": "Task input"},
                        },
                    },
                    skill_id=skill.id,
                    reliability=reliability,
                )
            )
        return tools

    def call_tool(self, request: SkillRequest) -> SkillResponse:
        """Execute a skill by ID and return steps + provenance."""
        skill = self._skills.get(request.skill_id)
        if skill is None:
            return SkillResponse(
                skill_id=request.skill_id,
                error=f"Skill '{request.skill_id}' not found",
            )

        reliability = _bayesian_lower_bound(skill.alpha, skill.beta_)
        steps = [step.to_dict() for step in skill.steps]

        return SkillResponse(
            skill_id=skill.id,
            steps=steps,
            provenance={
                "reliability": round(reliability, 4),
                "alpha": skill.alpha,
                "beta": skill.beta_,
                "use_count": skill.use_count,
                "source_task_id": skill.source_task_id,
                "confidence_at_extraction": skill.confidence_at_extraction,
            },
        )

    def import_skill(self, skill: Skill) -> ImportResult:
        """Import an external skill through quarantine."""
        qr = self._quarantine.evaluate(skill)

        if qr.status != QuarantineStatus.APPROVED:
            return ImportResult(
                accepted=False,
                skill_id=skill.id,
                violations=qr.violations,
                quarantine_result=qr,
            )

        self._skills[skill.id] = skill
        log.info("mcp.skill_imported skill_id=%s trust=%.4f", skill.id, qr.trust_score)

        return ImportResult(
            accepted=True,
            skill_id=skill.id,
            violations=[],
            quarantine_result=qr,
        )
