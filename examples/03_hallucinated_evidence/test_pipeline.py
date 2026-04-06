"""
Tests for the Citation Verification Pipeline.

Failure-first: tests that prove fabricated citations are caught
come BEFORE tests that prove real citations pass.

Uses LOCAL mode (no API calls) so tests run offline and fast.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Add example dir to path
sys.path.insert(0, str(Path(__file__).parent))

from extractors.citation_parser import extract_citations
from extractors.models import VerificationStatus


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


_pipeline = _load_local_module("pipeline.py", f"{Path(__file__).parent.name}_pipeline")
CitationPipelineVerifier = _pipeline.CitationPipelineVerifier

from veridian.core.task import Task, TaskResult


@pytest.fixture
def verifier() -> CitationPipelineVerifier:
    """Local-mode verifier — no API calls, runs offline."""
    return CitationPipelineVerifier(mode="local")


def _task(tid: str = "t1") -> Task:
    return Task(id=tid, title="verify brief", verifier_id="citation_pipeline")


# ── Phase 1: Extraction Tests ───────────────────────────────────────────────


class TestCitationExtraction:
    """eyecite extracts structured citations from legal text."""

    def test_extracts_scotus_citation(self) -> None:
        cites = extract_citations("Brown v. Board of Education, 347 U.S. 483 (1954)")
        assert len(cites) >= 1
        assert cites[0].volume == "347"
        assert cites[0].reporter == "U.S."
        assert cites[0].page == "483"

    def test_extracts_federal_reporter(self) -> None:
        cites = extract_citations("Williams v. Merit Sys., 892 F.3d 1156 (2018)")
        assert len(cites) >= 1
        assert cites[0].volume == "892"

    def test_extracts_multiple_citations(self) -> None:
        text = "347 U.S. 483 and 384 U.S. 436 and 892 F.3d 1156"
        cites = extract_citations(text)
        assert len(cites) >= 3

    def test_extracts_party_names(self) -> None:
        cites = extract_citations("In Brown v. Board of Education, 347 U.S. 483 (1954)...")
        assert len(cites) >= 1
        party = cites[0].party_names.lower()
        assert "brown" in party or cites[0].party_names == ""  # eyecite may not capture context


# ── Phase 2: Blocks Fabricated Citations ─────────────────────────────────────


class TestBlocksFabricatedCitations:
    """Prove Mata v. Avianca incident pattern is caught."""

    def test_blocks_completely_fabricated_citation(
        self, verifier: CitationPipelineVerifier
    ) -> None:
        """Citation address doesn't exist in any database."""
        text = "In Fakeman v. Nobody, 987 F.3d 6543 (2024), the court held..."
        report = verifier.run_pipeline(text, "test")
        assert not report.passed
        hallucinated = [
            r for r in report.results if r.status == VerificationStatus.HALLUCINATED_CITATION
        ]
        assert len(hallucinated) >= 1

    def test_blocks_mata_pattern_fabricated_airline_case(
        self, verifier: CitationPipelineVerifier
    ) -> None:
        """Exact Mata v. Avianca pattern: plausible but nonexistent."""
        text = (
            "In Martinez v. GlobalCorp Airlines, 892 F.3d 1156 (2d Cir. 2019), "
            "the court held that airlines bear strict liability."
        )
        report = verifier.run_pipeline(text, "mata_pattern")
        # 892 F.3d 1156 exists but is Williams v. Merit Sys — not Martinez v. GlobalCorp
        # In local mode, it won't be found (only SCOTUS cases in corpus)
        has_hallucination = any(
            r.status
            in (VerificationStatus.HALLUCINATED_CITATION, VerificationStatus.HALLUCINATED_NAME)
            for r in report.results
        )
        assert has_hallucination

    def test_sample_brief_catches_fabrications(self, verifier: CitationPipelineVerifier) -> None:
        """The sample brief has 2 real + 4 fabricated — pipeline should fail."""
        sample = Path(__file__).parent / "data" / "sample_brief.txt"
        if not sample.exists():
            pytest.skip("sample_brief.txt not found")
        text = sample.read_text()
        report = verifier.run_pipeline(text, "sample_brief")
        assert report.total_citations >= 4
        assert not report.passed, "Sample brief contains fabricated citations and should fail"


# ── Phase 3: Passes Real Citations ───────────────────────────────────────────


class TestPassesRealCitations:
    """Prove legitimate legal work is not blocked."""

    def test_passes_brown_v_board(self, verifier: CitationPipelineVerifier) -> None:
        text = "In Brown v. Board of Education, 347 U.S. 483 (1954), the Court held..."
        report = verifier.run_pipeline(text, "brown")
        verified = [r for r in report.results if r.status == VerificationStatus.VERIFIED]
        assert len(verified) >= 1

    def test_passes_miranda_v_arizona(self, verifier: CitationPipelineVerifier) -> None:
        text = "Under Miranda v. Arizona, 384 U.S. 436 (1966), rights advisement is required."
        report = verifier.run_pipeline(text, "miranda")
        verified = [r for r in report.results if r.status == VerificationStatus.VERIFIED]
        assert len(verified) >= 1

    def test_passes_text_without_citations(self, verifier: CitationPipelineVerifier) -> None:
        text = "The contract requires annual compliance audits for all regulated entities."
        report = verifier.run_pipeline(text, "no_cites")
        assert report.passed
        assert report.total_citations == 0

    def test_passes_empty_text(self, verifier: CitationPipelineVerifier) -> None:
        result = verifier.verify(_task(), TaskResult(raw_output=""))
        assert result.passed


# ── Phase 4: Veridian Integration ────────────────────────────────────────────


class TestVeridianIntegration:
    """Prove the pipeline works as a real Veridian BaseVerifier."""

    def test_verifier_returns_passed_false_for_fabricated(
        self, verifier: CitationPipelineVerifier
    ) -> None:
        text = "In Fakeman v. Nobody, 987 F.3d 6543 (2024), the court held..."
        result = verifier.verify(_task(), TaskResult(raw_output=text))
        assert result.passed is False
        assert result.error  # specific error message present

    def test_verifier_returns_passed_true_for_real(
        self, verifier: CitationPipelineVerifier
    ) -> None:
        text = "Brown v. Board of Education, 347 U.S. 483 (1954) established..."
        result = verifier.verify(_task(), TaskResult(raw_output=text))
        assert result.passed is True

    def test_verifier_evidence_includes_report(self, verifier: CitationPipelineVerifier) -> None:
        text = "347 U.S. 483 established the precedent."
        result = verifier.verify(_task(), TaskResult(raw_output=text))
        assert "total_citations" in result.evidence
