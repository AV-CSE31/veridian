"""
Tests for Problem 3: Hallucinated Evidence — Citation Grounding.
Each test reproduces the exact Mata v. Avianca pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from solution import check_brief, CitationGroundingVerifier, CASE_LAW_CORPUS


class TestBlocksFabricatedCitations:
    """Prove Mata v. Avianca incident cannot recur through Veridian."""

    def test_blocks_mata_pattern_fabricated_airline_case(self) -> None:
        """Exact Mata v. Avianca pattern: fabricated airline liability case."""
        ok, err, ev = check_brief(
            "In Martinez v. GlobalCorp, 892 F.3d 1156, the court held that "
            "airlines bear strict liability for service equipment injuries."
        )
        assert not ok
        assert ev.get("fabricated")
        assert any("Martinez" in f for f in ev["fabricated"])

    def test_blocks_plausible_fabrication(self) -> None:
        ok, _, ev = check_brief(
            "Henderson v. Pacific Air Transport, 445 F. Supp. 2d 891 confirms "
            "carriers owe heightened duty of care to elderly passengers."
        )
        assert not ok

    def test_blocks_mixed_real_and_fake(self) -> None:
        """Miranda is real, Chen v. Skybridge is fabricated."""
        ok, _, ev = check_brief(
            "While Miranda v. Arizona established rights advisement, "
            "Chen v. Skybridge Airways, 901 F.3d 234 extended this principle."
        )
        assert not ok
        fabricated = ev.get("fabricated", [])
        verified = ev.get("verified", [])
        assert any("Chen" in f for f in fabricated)
        assert any("Miranda" in v for v in verified)

    def test_blocks_multiple_fabricated_in_one_paragraph(self) -> None:
        ok, _, ev = check_brief(
            "See Thompson v. Meridian Corp, 567 F.3d 890; "
            "also Park v. United Digital, 712 F. Supp. 3d 445."
        )
        assert not ok
        assert len(ev.get("fabricated", [])) >= 2

    def test_error_message_names_fabricated_case(self) -> None:
        _, err, _ = check_brief("In Fakename v. Phantom Corp, the court ruled...")
        assert "Fakename" in err or "Phantom" in err


class TestPassesRealCitations:
    """Prove legitimate legal work completes without obstruction."""

    def test_passes_brown_v_board(self) -> None:
        ok, _, ev = check_brief(
            "As held in Brown v. Board of Education, segregation violates "
            "the Equal Protection Clause."
        )
        assert ok
        assert any("Brown" in v for v in ev.get("verified", []))

    def test_passes_miranda_v_arizona(self) -> None:
        ok, _, _ = check_brief(
            "Under Miranda v. Arizona, custodial interrogation statements "
            "are inadmissible without rights advisement."
        )
        assert ok

    def test_passes_text_with_no_citations(self) -> None:
        ok, _, ev = check_brief(
            "The contract specifies a 30-day notice period for termination."
        )
        assert ok
        assert ev.get("citations_found") == 0

    def test_passes_empty_text(self) -> None:
        ok, _, _ = check_brief("")
        assert ok

    def test_corpus_has_twenty_cases(self) -> None:
        assert len(CASE_LAW_CORPUS) == 20
