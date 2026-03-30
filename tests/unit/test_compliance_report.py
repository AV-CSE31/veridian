"""
Tests for veridian.observability.compliance_report — Compliance report generator.
TDD: RED phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.observability.compliance_report import (
    ComplianceReport,
    ComplianceStandard,
    ComplianceReportGenerator,
)
from veridian.observability.proof_chain import ProofChain, ProofEntry


# ── ComplianceStandard ──────────────────────────────────────────────────────


class TestComplianceStandard:
    def test_eu_ai_act_exists(self) -> None:
        assert ComplianceStandard.EU_AI_ACT.value == "eu_ai_act"

    def test_nist_ai_rmf_exists(self) -> None:
        assert ComplianceStandard.NIST_AI_RMF.value == "nist_ai_rmf"

    def test_owasp_agentic_exists(self) -> None:
        assert ComplianceStandard.OWASP_AGENTIC.value == "owasp_agentic"


# ── ComplianceReportGenerator ───────────────────────────────────────────────


class TestComplianceReportGenerator:
    def _make_chain(self) -> ProofChain:
        chain = ProofChain()
        for i in range(3):
            chain.append(ProofEntry(
                task_id=f"t{i}",
                task_spec_hash=f"spec{i}",
                model_version="gemini/gemini-2.5-flash",
                verifier_config_hash=f"vcfg{i}",
                verification_evidence={"passed": True},
                policy_attestation=["safety_v1"],
            ))
        return chain

    def test_generates_eu_ai_act_report(self) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        assert report.standard == ComplianceStandard.EU_AI_ACT
        assert report.total_tasks == 3
        assert report.chain_intact is True

    def test_generates_owasp_report(self) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.OWASP_AGENTIC)
        assert report.standard == ComplianceStandard.OWASP_AGENTIC

    def test_report_to_dict(self) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        d = report.to_dict()
        assert "standard" in d
        assert "chain_intact" in d
        assert "total_tasks" in d

    def test_report_to_markdown(self) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        md = report.to_markdown()
        assert "EU AI Act" in md or "eu_ai_act" in md
        assert "intact" in md.lower() or "verified" in md.lower()

    def test_report_flags_broken_chain(self) -> None:
        chain = self._make_chain()
        chain._entries[1].task_spec_hash = "TAMPERED"
        gen = ComplianceReportGenerator(proof_chain=chain)
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        assert report.chain_intact is False

    def test_report_includes_model_versions(self) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        assert "gemini/gemini-2.5-flash" in report.model_versions

    def test_save_report(self, tmp_path: Path) -> None:
        gen = ComplianceReportGenerator(proof_chain=self._make_chain())
        report = gen.generate(ComplianceStandard.EU_AI_ACT)
        path = tmp_path / "compliance.md"
        report.save(path)
        assert path.exists()
        assert "EU AI Act" in path.read_text() or "eu_ai_act" in path.read_text()
