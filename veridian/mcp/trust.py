"""
veridian.mcp.trust
───────────────────
Federated Trust — cross-organization skill sharing with independent trust scores.

Each organization has an independent trust score that increases slowly on
verified success and decreases quickly on violations. Skills from unknown
or untrusted orgs always go through quarantine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FederatedTrustManager",
    "OrgTrustRecord",
    "SkillProvenance",
    "TrustDecision",
]

log = logging.getLogger(__name__)


@dataclass
class OrgTrustRecord:
    """Trust record for a single organization."""

    org_id: str = ""
    trust_score: float = 0.0
    successes: int = 0
    violations: int = 0

    def record_success(self) -> None:
        """Slow ratchet up: +0.02 per verified success, capped at 1.0."""
        self.successes += 1
        self.trust_score = min(1.0, self.trust_score + 0.02)

    def record_violation(self) -> None:
        """Fast ratchet down: -0.20 per violation, floored at 0.0."""
        self.violations += 1
        self.trust_score = max(0.0, self.trust_score - 0.20)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "trust_score": round(self.trust_score, 4),
            "successes": self.successes,
            "violations": self.violations,
        }


@dataclass
class SkillProvenance:
    """Provenance chain for a shared skill."""

    skill_id: str = ""
    origin_org: str = ""
    verification_chain: list[str] = field(default_factory=list)
    signed_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "origin_org": self.origin_org,
            "verification_chain": self.verification_chain,
            "signed_hash": self.signed_hash,
        }


@dataclass
class TrustDecision:
    """Result of evaluating a skill import from another organization."""

    accepted: bool = False
    requires_quarantine: bool = True
    org_trust: float = 0.0
    skill_reliability: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "requires_quarantine": self.requires_quarantine,
            "org_trust": round(self.org_trust, 4),
            "skill_reliability": round(self.skill_reliability, 4),
            "reason": self.reason,
        }


class FederatedTrustManager:
    """Manages trust relationships across organizations for skill sharing.

    Trust is independent per org. Skills from trusted orgs skip quarantine
    if both org trust and skill reliability meet thresholds.
    """

    def __init__(
        self,
        trust_threshold: float = 0.60,
        reliability_threshold: float = 0.70,
    ) -> None:
        self._orgs: dict[str, OrgTrustRecord] = {}
        self._trust_threshold = trust_threshold
        self._reliability_threshold = reliability_threshold

    def register_org(self, org_id: str) -> None:
        """Register a new organization with zero trust."""
        if org_id not in self._orgs:
            self._orgs[org_id] = OrgTrustRecord(org_id=org_id)

    def get_trust(self, org_id: str) -> float:
        """Get current trust score for an org."""
        record = self._orgs.get(org_id)
        return record.trust_score if record else 0.0

    def record_outcome(self, org_id: str, success: bool) -> None:
        """Record a verified skill outcome for an org."""
        if org_id not in self._orgs:
            self.register_org(org_id)
        record = self._orgs[org_id]
        if success:
            record.record_success()
        else:
            record.record_violation()

    def evaluate_import(
        self,
        provenance: SkillProvenance,
        skill_reliability: float,
    ) -> TrustDecision:
        """Evaluate whether to accept a skill import from another org."""
        org_id = provenance.origin_org
        record = self._orgs.get(org_id)

        if record is None:
            return TrustDecision(
                accepted=skill_reliability >= self._reliability_threshold,
                requires_quarantine=True,
                org_trust=0.0,
                skill_reliability=skill_reliability,
                reason=f"Unknown org '{org_id}' — quarantine required",
            )

        org_trust = record.trust_score
        trusted = org_trust >= self._trust_threshold
        reliable = skill_reliability >= self._reliability_threshold

        if trusted and reliable:
            return TrustDecision(
                accepted=True,
                requires_quarantine=False,
                org_trust=org_trust,
                skill_reliability=skill_reliability,
                reason="Trusted org with reliable skill — quarantine skipped",
            )

        if reliable:
            return TrustDecision(
                accepted=True,
                requires_quarantine=True,
                org_trust=org_trust,
                skill_reliability=skill_reliability,
                reason="Skill reliable but org trust below threshold — quarantine required",
            )

        return TrustDecision(
            accepted=False,
            requires_quarantine=True,
            org_trust=org_trust,
            skill_reliability=skill_reliability,
            reason=f"Org trust {org_trust:.2f} and skill reliability {skill_reliability:.2f} "
            f"below thresholds",
        )

    def list_orgs(self) -> list[OrgTrustRecord]:
        """Return all registered org trust records."""
        return list(self._orgs.values())
