"""Tests for Problem 6: EU AI Act — real ProofChain + ComplianceReport."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


_solution = _load_local_module("solution.py", f"{Path(__file__).parent.name}_solution")
build_auditable_pipeline = _solution.build_auditable_pipeline

from veridian.observability.compliance_report import ComplianceReportGenerator, ComplianceStandard
from veridian.observability.proof_chain import ProofChain


class TestDetectsTampering:
    """Article 12 requires tamper-evident logs."""

    def test_detects_modified_entry(self) -> None:
        chain = build_auditable_pipeline()
        chain._entries[2].task_spec_hash = "TAMPERED"
        assert chain.verify() is False

    def test_compliance_flags_broken_chain(self) -> None:
        chain = build_auditable_pipeline()
        chain._entries[1].task_spec_hash = "TAMPERED"
        report = ComplianceReportGenerator(proof_chain=chain).generate(ComplianceStandard.EU_AI_ACT)
        assert report.chain_intact is False

    def test_hmac_signatures_present(self) -> None:
        chain = build_auditable_pipeline()
        for entry in chain._entries:
            assert entry.chain_signature != ""
            assert len(entry.chain_signature) == 64


class TestCompliantChain:
    """Prove valid chains pass all checks."""

    def test_intact_chain(self) -> None:
        assert build_auditable_pipeline().verify() is True

    def test_report_total_tasks(self) -> None:
        report = ComplianceReportGenerator(proof_chain=build_auditable_pipeline()).generate(
            ComplianceStandard.EU_AI_ACT
        )
        assert report.total_tasks == 5

    def test_report_tracks_model_version(self) -> None:
        report = ComplianceReportGenerator(proof_chain=build_auditable_pipeline()).generate(
            ComplianceStandard.EU_AI_ACT
        )
        assert "gemini/gemini-2.5-flash" in report.model_versions

    def test_report_tracks_policies(self) -> None:
        report = ComplianceReportGenerator(proof_chain=build_auditable_pipeline()).generate(
            ComplianceStandard.EU_AI_ACT
        )
        assert len(report.policies_active) > 0

    def test_chain_links_correctly(self) -> None:
        chain = build_auditable_pipeline()
        for i in range(1, len(chain._entries)):
            assert chain._entries[i].previous_hash == chain._entries[i - 1].compute_hash()

    def test_save_and_load_preserves_integrity(self, tmp_path: Path) -> None:
        chain = build_auditable_pipeline()
        path = tmp_path / "chain.jsonl"
        chain.save(path)
        loaded = ProofChain.load(path)
        assert loaded.verify() is True
        assert len(loaded) == 5
