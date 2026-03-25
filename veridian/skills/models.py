"""
veridian.skills.models
──────────────────────
Core data models for the SkillLibrary: Skill, SkillStep, SkillCandidate.

Research basis:
  Voyager (Wang et al. 2023): skills indexed by embedding of description.
  MACLA (arXiv 2512.18950): Bayesian reliability scoring with Beta distribution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Skill", "SkillStep", "SkillCandidate"]


@dataclass
class SkillStep:
    """A single executable step within a verified procedure."""

    description: str
    command: str | None = None
    verifier_hint: str | None = None
    exit_code_expected: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "description": self.description,
            "command": self.command,
            "verifier_hint": self.verifier_hint,
            "exit_code_expected": self.exit_code_expected,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillStep:
        """Deserialize from dict."""
        return cls(
            description=d.get("description", ""),
            command=d.get("command"),
            verifier_hint=d.get("verifier_hint"),
            exit_code_expected=d.get("exit_code_expected", 0),
        )


@dataclass
class Skill:
    """
    A verified, reusable procedure extracted from a completed task.

    INVARIANT: Only extracted from TaskStatus.DONE tasks that passed verification.
    Bayesian reliability follows MACLA pattern: Beta(alpha, beta_) prior,
    updated on each reuse outcome.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    trigger: str = ""  # natural-language description of when to apply
    domain: str = "generic"  # "legal" | "compliance" | "code-migration" | "generic"
    verifier_id: str = "bash_exit"
    steps: list[SkillStep] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    context_requirements: list[str] = field(default_factory=list)
    confidence_at_extraction: float = 1.0
    source_task_id: str = ""
    source_run_id: str = ""

    # Bayesian reliability — MACLA Beta(alpha, beta_) distribution
    # alpha = successes + 1  (prior: 1 success)
    # beta_ = failures  + 1  (prior: 1 failure — avoids overfitting on first use)
    alpha: float = 1.0
    beta_: float = 1.0  # beta_ to avoid clash with stdlib beta()
    use_count: int = 0

    # Cached embedding vector (set by SkillStore on first save)
    embedding: list[float] = field(default_factory=list)

    # ── Bayesian reliability properties ───────────────────────────────────────

    @property
    def reliability_score(self) -> float:
        """Mean of Beta distribution: alpha / (alpha + beta_)."""
        return self.alpha / (self.alpha + self.beta_)

    @property
    def bayesian_lower_bound(self) -> float:
        """
        Conservative reliability estimate: mean - 1.96 * std_error.
        Used for ranking: penalises low-use skills more than well-tested ones.
        """
        n = self.alpha + self.beta_
        p = self.reliability_score
        variance = (p * (1.0 - p)) / n
        return float(max(0.0, p - 1.96 * (variance**0.5)))

    def record_success(self) -> None:
        """Observed successful reuse — increment alpha (successes) and use_count."""
        self.alpha += 1.0
        self.use_count += 1

    def record_failure(self) -> None:
        """Observed failed reuse — increment beta_ (failures) and use_count."""
        self.beta_ += 1.0
        self.use_count += 1

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "domain": self.domain,
            "verifier_id": self.verifier_id,
            "steps": [s.to_dict() for s in self.steps],
            "tools_used": self.tools_used,
            "context_requirements": self.context_requirements,
            "confidence_at_extraction": self.confidence_at_extraction,
            "source_task_id": self.source_task_id,
            "source_run_id": self.source_run_id,
            "alpha": self.alpha,
            "beta_": self.beta_,
            "use_count": self.use_count,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Skill:
        """Deserialize from a dict produced by to_dict()."""
        steps = [SkillStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            trigger=d.get("trigger", ""),
            domain=d.get("domain", "generic"),
            verifier_id=d.get("verifier_id", "bash_exit"),
            steps=steps,
            tools_used=d.get("tools_used", []),
            context_requirements=d.get("context_requirements", []),
            confidence_at_extraction=d.get("confidence_at_extraction", 1.0),
            source_task_id=d.get("source_task_id", ""),
            source_run_id=d.get("source_run_id", ""),
            alpha=d.get("alpha", 1.0),
            beta_=d.get("beta_", 1.0),
            use_count=d.get("use_count", 0),
            embedding=d.get("embedding", []),
        )


@dataclass
class SkillCandidate:
    """
    Intermediate representation: a DONE task that may qualify as a reusable Skill.
    Produced by SkillExtractor, evaluated by SkillAdmissionControl.
    """

    task_id: str
    run_id: str
    task_title: str
    task_description: str
    verifier_id: str
    confidence: float
    retry_count: int
    bash_outputs: list[dict[str, Any]]
    structured_output: dict[str, Any]
    domain_hint: str = ""
