"""
veridian.skills.blast_radius
─────────────────────────────
Contamination blast radius analysis for compromised skills.

When a skill is flagged as compromised:
  1. Trace all tasks that used this skill
  2. Trace all downstream tasks consuming those outputs
  3. Trace all skills extracted from affected tasks
  4. Generate impact report with total scope
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["BlastRadiusAnalyzer", "ImpactReport"]


@dataclass
class ImpactReport:
    """Impact report for a compromised skill."""

    compromised_skill_id: str = ""
    affected_tasks: list[str] = field(default_factory=list)
    downstream_tasks: list[str] = field(default_factory=list)
    downstream_skills: list[str] = field(default_factory=list)
    total_impact_scope: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "compromised_skill_id": self.compromised_skill_id,
            "affected_tasks": self.affected_tasks,
            "downstream_tasks": self.downstream_tasks,
            "downstream_skills": self.downstream_skills,
            "total_impact_scope": self.total_impact_scope,
        }

    def to_markdown(self) -> str:
        """Generate impact report markdown."""
        lines = [
            f"# Blast Radius Report — {self.compromised_skill_id}",
            "",
            f"**Total impact scope:** {self.total_impact_scope}",
            "",
        ]
        if self.total_impact_scope == 0:
            lines.append("No impact detected. Skill was not used by any tasks.")
            lines.append("")
            return "\n".join(lines)

        if self.affected_tasks:
            lines.append("## Directly Affected Tasks")
            for tid in self.affected_tasks:
                lines.append(f"- {tid}")
            lines.append("")

        if self.downstream_tasks:
            lines.append("## Downstream Tasks (transitive)")
            for tid in self.downstream_tasks:
                lines.append(f"- {tid}")
            lines.append("")

        if self.downstream_skills:
            lines.append("## Downstream Skills (potentially contaminated)")
            for sid in self.downstream_skills:
                lines.append(f"- {sid}")
            lines.append("")

        return "\n".join(lines)


class BlastRadiusAnalyzer:
    """Traces contamination from a compromised skill through the task graph.

    Args:
        task_provenance: task_id -> {skills_used: [...], downstream_tasks: [...]}
        skill_provenance: skill_id -> {source_task_id: ...}
    """

    def __init__(
        self,
        task_provenance: dict[str, dict[str, list[str]]],
        skill_provenance: dict[str, dict[str, str]],
    ) -> None:
        self._task_prov = task_provenance
        self._skill_prov = skill_provenance

    def analyze(self, compromised_skill_id: str) -> ImpactReport:
        """Trace all impact from a compromised skill."""
        # Step 1: Find all tasks that directly used this skill
        directly_affected: set[str] = set()
        for task_id, prov in self._task_prov.items():
            skills_used = prov.get("skills_used", [])
            if compromised_skill_id in skills_used:
                directly_affected.add(task_id)

        # Step 2: Trace downstream tasks (BFS)
        downstream_tasks: set[str] = set()
        queue = list(directly_affected)
        visited: set[str] = set(directly_affected)

        while queue:
            current = queue.pop(0)
            prov = self._task_prov.get(current, {})
            for dt in prov.get("downstream_tasks", []):
                if dt not in visited:
                    visited.add(dt)
                    downstream_tasks.add(dt)
                    queue.append(dt)

        # Step 3: Find skills extracted from any affected task
        all_affected = directly_affected | downstream_tasks
        downstream_skills: set[str] = set()
        for skill_id, sprov in self._skill_prov.items():
            source_task = sprov.get("source_task_id", "")
            if source_task in all_affected and skill_id != compromised_skill_id:
                downstream_skills.add(skill_id)

        total = len(directly_affected) + len(downstream_tasks) + len(downstream_skills)

        return ImpactReport(
            compromised_skill_id=compromised_skill_id,
            affected_tasks=sorted(directly_affected),
            downstream_tasks=sorted(downstream_tasks),
            downstream_skills=sorted(downstream_skills),
            total_impact_scope=total,
        )
