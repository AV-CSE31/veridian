"""
tests.unit.test_threat_model
────────────────────────────
Tests for the formal threat model registry.
"""

from __future__ import annotations

import importlib
import json

import pytest

from veridian.core.threat_model import (
    GAPS,
    STATUSES,
    ThreatGap,
    UnknownThreatGap,
    as_evidence,
    get_gap,
)


class TestThreatGap:
    def test_construct(self) -> None:
        gap = ThreatGap(
            gap_id="GX",
            title="t",
            attack_vector="v",
            defense_components=("a.b.C",),
        )
        assert gap.gap_id == "GX"
        assert gap.status == "implemented"

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="gap_id"):
            ThreatGap(gap_id="", title="t", attack_vector="v", defense_components=())

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValueError, match="title"):
            ThreatGap(gap_id="GX", title="", attack_vector="v", defense_components=())

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ThreatGap(
                gap_id="GX",
                title="t",
                attack_vector="v",
                defense_components=(),
                status="unknown",
            )

    def test_to_dict_round_trip_json(self) -> None:
        gap = GAPS["G5"]
        payload = json.dumps(gap.to_dict())
        restored = json.loads(payload)
        assert restored["gap_id"] == "G5"
        assert isinstance(restored["defense_components"], list)


class TestRegistry:
    def test_known_gaps(self) -> None:
        assert set(GAPS) == {"G1", "G2", "G3", "G4", "G5"}

    def test_get_gap_returns_registered(self) -> None:
        gap = get_gap("G5")
        assert gap.title == "Agent Communication Injection via tool output"

    def test_get_gap_unknown_raises(self) -> None:
        with pytest.raises(UnknownThreatGap, match="G99"):
            get_gap("G99")

    def test_all_gaps_implemented(self) -> None:
        # All currently registered gaps should be implemented; partial/planned
        # entries require an explicit roadmap follow-up.
        for gap in GAPS.values():
            assert gap.status in STATUSES
            assert gap.status == "implemented"

    def test_defense_components_resolve_to_real_modules(self) -> None:
        """Every defense component string should resolve to a real attribute."""
        for gap in GAPS.values():
            for component in gap.defense_components:
                module_path, _, attr = component.rpartition(".")
                module = importlib.import_module(module_path)
                assert hasattr(module, attr), f"{component} missing from {module_path}"


class TestEvidence:
    def test_as_evidence_is_json_serializable(self) -> None:
        evidence = as_evidence()
        payload = json.dumps(evidence)
        assert "G5" in payload

    def test_as_evidence_includes_version(self) -> None:
        evidence = as_evidence()
        assert evidence["version"] == 1
        assert len(evidence["gaps"]) == len(GAPS)
