"""
veridian.core.threat_model
──────────────────────────
Formal threat model: the named attack vectors Veridian defends against and
the defense components that implement those mitigations.

Each :class:`ThreatGap` pins:

* the canonical gap id (``"G1"`` .. ``"G5"``) and human-readable title
* the attack vector being defended against
* the implementation modules / classes that constitute the defense
* the research basis (citations) that motivated the defense
* implementation status (``"implemented"``, ``"partial"``, ``"planned"``)

The threat model is a static registry — the gaps are versioned with the
codebase, not with runtime config. Operators can call
:func:`as_evidence` to export the current model as an audit artifact.

The naming is aligned with the in-code references in
``veridian.loop.trusted_executor`` (Gap 5),
``veridian.verify.builtin.semantic_grounding`` (Gap 1),
``veridian.verify.builtin.confidence`` (Gap 2),
``veridian.hooks.builtin.cross_run_consistency`` (Gap 3), and
``veridian.core.quality_gate`` (Gap 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Final

from veridian.core.exceptions import VeridianError

__all__ = [
    "GAPS",
    "STATUSES",
    "ThreatGap",
    "UnknownThreatGap",
    "as_evidence",
    "get_gap",
]


STATUSES: Final[frozenset[str]] = frozenset({"implemented", "partial", "planned"})


class UnknownThreatGap(VeridianError):
    """Raised when :func:`get_gap` is given an unknown gap id."""


@dataclass(frozen=True)
class ThreatGap:
    """A named threat vector and its defense components."""

    gap_id: str
    title: str
    attack_vector: str
    defense_components: tuple[str, ...]
    research_basis: tuple[str, ...] = field(default_factory=tuple)
    status: str = "implemented"

    def __post_init__(self) -> None:
        if not self.gap_id:
            raise ValueError("gap_id must be non-empty")
        if not self.title:
            raise ValueError("title must be non-empty")
        if self.status not in STATUSES:
            raise ValueError(f"status={self.status!r} not in {sorted(STATUSES)}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        data = asdict(self)
        data["defense_components"] = list(self.defense_components)
        data["research_basis"] = list(self.research_basis)
        return data


# ── Registered gaps ────────────────────────────────────────────────────────────
#
# Each entry corresponds to an in-code ``# Gap N`` annotation. When you add a
# new gap mitigation in the codebase, register it here so it appears in audit
# evidence exports.

_GAPS: tuple[ThreatGap, ...] = (
    ThreatGap(
        gap_id="G1",
        title="Hallucinated grounding",
        attack_vector=(
            "Agent produces structured output that references entities or "
            "facts not present in the task context, claiming groundedness "
            "the verifier cannot independently check."
        ),
        defense_components=(
            "veridian.verify.builtin.semantic_grounding.SemanticGroundingVerifier",
            "veridian.verify.builtin.embedding_grounding.EmbeddingGroundingVerifier",
            "veridian.verify.builtin.quote.QuoteMatchVerifier",
        ),
        research_basis=(),
        status="implemented",
    ),
    ThreatGap(
        gap_id="G2",
        title="Overconfident single-sample decisions",
        attack_vector=(
            "Agent emits a confident-looking answer on a single sample. The "
            "verifier passes deterministically but the decision is fragile "
            "under resampling — small input perturbations flip the verdict."
        ),
        defense_components=(
            "veridian.verify.builtin.confidence.ConfidenceScore",
            "veridian.verify.builtin.confidence.SelfConsistencyVerifier",
        ),
        research_basis=(),
        status="implemented",
    ),
    ThreatGap(
        gap_id="G3",
        title="Cross-run drift",
        attack_vector=(
            "Two runs on the same task produce subtly different outputs that "
            "still pass individual verifiers. Drift accumulates silently across "
            "deployments and erodes audit reproducibility."
        ),
        defense_components=(
            "veridian.hooks.builtin.cross_run_consistency.CrossRunConsistencyHook",
            "veridian.hooks.builtin.drift_detector.DriftDetectorHook",
        ),
        research_basis=(),
        status="implemented",
    ),
    ThreatGap(
        gap_id="G4",
        title="Single-verifier blast radius",
        attack_vector=(
            "An entire task graph is gated on one verifier. A bug in that "
            "verifier or a single LLM-judge false-positive lets an unsafe "
            "transition cascade across downstream tasks."
        ),
        defense_components=(
            "veridian.core.quality_gate.TaskQualityGate",
            "veridian.verify.builtin.composite.CompositeVerifier",
            "veridian.verify.builtin.any_of.AnyOfVerifier",
        ),
        research_basis=(),
        status="implemented",
    ),
    ThreatGap(
        gap_id="G5",
        title="Agent Communication Injection via tool output",
        attack_vector=(
            "A bash/MCP tool returns output containing LLM instruction "
            "patterns (e.g. 'SYSTEM: ignore previous instructions'). The "
            "output is injected verbatim into the next agent prompt, "
            "bypassing verifier logic by pre-stuffing the answer."
        ),
        defense_components=(
            "veridian.loop.trusted_executor.TrustedExecutor",
            "veridian.loop.trusted_executor.OutputSanitizer",
            "veridian.loop.trusted_executor.BashOutput",
        ),
        research_basis=(
            "ACI attacks — arXiv:2507.21146",
            "Trusted AI Agents (Omega) — arXiv:2512.05951",
            "OWASP Agentic AI 2025 (AAI005, AAI007)",
        ),
        status="implemented",
    ),
)


GAPS: Mapping[str, ThreatGap] = MappingProxyType({gap.gap_id: gap for gap in _GAPS})


def get_gap(gap_id: str) -> ThreatGap:
    """Return the registered :class:`ThreatGap` for ``gap_id``.

    Raises:
        UnknownThreatGap: if ``gap_id`` is not registered.
    """
    try:
        return GAPS[gap_id]
    except KeyError as exc:
        raise UnknownThreatGap(f"unknown gap_id={gap_id!r}; known: {sorted(GAPS)}") from exc


def as_evidence() -> dict[str, Any]:
    """Return the full threat model as a JSON-serializable audit artifact."""
    return {
        "version": 1,
        "gaps": [gap.to_dict() for gap in _GAPS],
    }
