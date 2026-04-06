"""
Tests for veridian.mcp.trust — Federated Trust scoring.
TDD: RED phase.
"""

from __future__ import annotations

from veridian.mcp.trust import (
    FederatedTrustManager,
    OrgTrustRecord,
    SkillProvenance,
    TrustDecision,
)

# ── OrgTrustRecord ──────────────────────────────────────────────────────────


class TestOrgTrustRecord:
    def test_new_org_starts_with_zero_trust(self) -> None:
        record = OrgTrustRecord(org_id="org-1")
        assert record.trust_score == 0.0

    def test_record_success_increases_trust(self) -> None:
        record = OrgTrustRecord(org_id="org-1")
        record.record_success()
        assert record.trust_score > 0.0

    def test_record_violation_decreases_trust(self) -> None:
        record = OrgTrustRecord(org_id="org-1", trust_score=0.8)
        record.record_violation()
        assert record.trust_score < 0.8

    def test_trust_never_exceeds_one(self) -> None:
        record = OrgTrustRecord(org_id="org-1", trust_score=0.99)
        for _ in range(100):
            record.record_success()
        assert record.trust_score <= 1.0

    def test_trust_never_below_zero(self) -> None:
        record = OrgTrustRecord(org_id="org-1", trust_score=0.1)
        for _ in range(100):
            record.record_violation()
        assert record.trust_score >= 0.0

    def test_to_dict(self) -> None:
        record = OrgTrustRecord(org_id="org-1", trust_score=0.75)
        d = record.to_dict()
        assert d["org_id"] == "org-1"
        assert d["trust_score"] == 0.75


# ── SkillProvenance ─────────────────────────────────────────────────────────


class TestSkillProvenance:
    def test_creates_provenance(self) -> None:
        prov = SkillProvenance(
            skill_id="s1",
            origin_org="org-a",
            verification_chain=["quarantine", "tool_safety", "canary"],
        )
        assert prov.origin_org == "org-a"
        assert len(prov.verification_chain) == 3

    def test_to_dict(self) -> None:
        prov = SkillProvenance(skill_id="s1", origin_org="org-a")
        d = prov.to_dict()
        assert "skill_id" in d
        assert "origin_org" in d


# ── FederatedTrustManager ───────────────────────────────────────────────────


class TestFederatedTrustManager:
    def test_creates_manager(self) -> None:
        mgr = FederatedTrustManager()
        assert mgr is not None

    def test_register_org(self) -> None:
        mgr = FederatedTrustManager()
        mgr.register_org("org-a")
        assert mgr.get_trust("org-a") == 0.0

    def test_evaluate_skill_from_trusted_org(self) -> None:
        mgr = FederatedTrustManager()
        mgr.register_org("org-a")
        # Build trust
        for _ in range(20):
            mgr.record_outcome("org-a", success=True)

        prov = SkillProvenance(skill_id="s1", origin_org="org-a")
        decision = mgr.evaluate_import(prov, skill_reliability=0.85)
        assert decision.accepted is True

    def test_reject_skill_from_untrusted_org(self) -> None:
        mgr = FederatedTrustManager()
        mgr.register_org("org-bad")
        mgr.record_outcome("org-bad", success=False)  # violation

        prov = SkillProvenance(skill_id="s1", origin_org="org-bad")
        decision = mgr.evaluate_import(prov, skill_reliability=0.50)
        assert decision.accepted is False

    def test_unknown_org_requires_quarantine(self) -> None:
        mgr = FederatedTrustManager()
        prov = SkillProvenance(skill_id="s1", origin_org="unknown-org")
        decision = mgr.evaluate_import(prov, skill_reliability=0.85)
        assert decision.requires_quarantine is True

    def test_trust_decision_to_dict(self) -> None:
        decision = TrustDecision(
            accepted=True,
            requires_quarantine=False,
            org_trust=0.85,
            skill_reliability=0.90,
            reason="Trusted org with high reliability skill",
        )
        d = decision.to_dict()
        assert d["accepted"] is True
        assert d["org_trust"] == 0.85

    def test_list_orgs(self) -> None:
        mgr = FederatedTrustManager()
        mgr.register_org("org-a")
        mgr.register_org("org-b")
        orgs = mgr.list_orgs()
        assert len(orgs) == 2
