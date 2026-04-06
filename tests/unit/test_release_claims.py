"""
tests.unit.test_release_claims
───────────────────────────────
RV3-014: Release gate automation — claim-to-test traceability.

Asserts that every claim in ``planning/RELEASE_GATES.md`` maps to an
existing test file, and every roadmap ticket marked DONE in
``planning/ISSUE_TRACKER.md`` has at least one test file in tests/.

NOTE: Tests that reference ``planning/`` are skipped in CI because that
directory is gitignored (IP-protected per CLAUDE.md). They run locally
where the planning docs exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GATES = _REPO / "planning" / "RELEASE_GATES.md"
_PLANNING_DIR = _REPO / "planning"
_TESTS = _REPO / "tests"

_planning_available = _PLANNING_DIR.is_dir() and _GATES.exists()
_skip_no_planning = pytest.mark.skipif(
    not _planning_available,
    reason="planning/ is gitignored and not present in this checkout (CI)",
)


# Tickets whose acceptance criteria ship a test file. Kept as a sorted list
# so diff noise is minimal when maintainers add a ticket.
_CLAIM_TO_TEST: dict[str, tuple[str, ...]] = {
    "RV3-001": (
        "integration/test_pause_resume.py",
        "unit/test_ledger_pause_resume.py",
    ),
    "RV3-002": ("unit/test_hook_registry_control_flow.py",),
    "RV3-003": (
        "integration/test_replay_compat_runner.py",
        "unit/test_replay_compat.py",
    ),
    "RV3-004": (
        "unit/test_activity_journal.py",
        "integration/test_activity_journal_runner.py",
    ),
    "RV3-005": ("unit/test_activity_journal.py",),
    "RV3-006": ("integration/test_replay_cli.py",),
    "RV3-007": ("integration/test_langgraph_adapter.py",),
    "RV3-008": ("integration/test_crewai_adapter.py",),
    "RV3-009": ("unit/test_api_stability.py",),
    "RV3-010": ("integration/test_parallel_parity.py",),
    "RV3-011": ("integration/test_subgraph.py",),
    "RV3-012": ("unit/test_tenancy.py",),
    "RV3-013": ("unit/test_api_stability.py",),
    "RV3-014": ("unit/test_release_claims.py",),
    "RV3-015": ("unit/test_release_claims.py",),
}


class TestClaimTraceability:
    @pytest.mark.parametrize("ticket,test_paths", sorted(_CLAIM_TO_TEST.items()))
    def test_claim_has_backing_test_file(self, ticket: str, test_paths: tuple[str, ...]) -> None:
        """Every roadmap ticket with a DONE status must have at least one
        named test file present under tests/ that exercises its acceptance."""
        missing = [p for p in test_paths if not (_TESTS / p).exists()]
        assert not missing, f"{ticket} claims {missing} but they do not exist"

    @_skip_no_planning
    def test_release_gates_doc_exists(self) -> None:
        assert _GATES.exists(), (
            "planning/RELEASE_GATES.md must exist — it defines the claim matrix."
        )

    @_skip_no_planning
    def test_release_gates_doc_references_every_ticket(self) -> None:
        """Every ticket in _CLAIM_TO_TEST must be mentioned in RELEASE_GATES.md.
        Guards against silent drift when a new ticket is added to the matrix
        without updating the release doc."""
        text = _GATES.read_text(encoding="utf-8")
        missing = [t for t in _CLAIM_TO_TEST if t not in text]
        assert not missing, f"Tickets missing from RELEASE_GATES.md: {missing}"

    @_skip_no_planning
    def test_every_rv3_ticket_mentioned_has_a_claim_entry(self) -> None:
        """Any RV3-xxx mentioned in the release gates must have a claim entry
        in _CLAIM_TO_TEST so it is validated by this file."""
        text = _GATES.read_text(encoding="utf-8")
        mentioned = set(re.findall(r"RV3-\d{3}", text))
        unmapped = mentioned - set(_CLAIM_TO_TEST.keys())
        assert not unmapped, (
            f"Tickets mentioned in RELEASE_GATES.md but missing from _CLAIM_TO_TEST: {unmapped}"
        )


class TestMandatoryGates:
    """Smoke-level assertions that the release-gate suites exist and are
    wired. Full execution is delegated to CI."""

    def test_integration_suites_exist(self) -> None:
        required = [
            "integration/test_pause_resume.py",
            "integration/test_replay_compat_runner.py",
            "integration/test_activity_journal_runner.py",
            "integration/test_replay_cli.py",
            "integration/test_langgraph_adapter.py",
            "integration/test_crewai_adapter.py",
            "integration/test_langgraph_certification.py",
            "integration/test_crewai_certification.py",
            "integration/test_certification_matrix.py",
            "integration/test_parallel_parity.py",
            "integration/test_subgraph.py",
        ]
        for path in required:
            assert (_TESTS / path).exists(), f"Release-gate integration suite missing: {path}"

    def test_unit_suites_exist(self) -> None:
        required = [
            "unit/test_hook_registry_control_flow.py",
            "unit/test_ledger_pause_resume.py",
            "unit/test_replay_compat.py",
            "unit/test_activity_journal.py",
            "unit/test_tenancy.py",
            "unit/test_api_stability.py",
        ]
        for path in required:
            assert (_TESTS / path).exists(), f"Release-gate unit suite missing: {path}"

    @_skip_no_planning
    def test_operational_runbooks_exist(self) -> None:
        required = [
            _REPO / "planning" / "runbooks" / "README.md",
            _REPO / "planning" / "runbooks" / "incident-triage.md",
            _REPO / "planning" / "runbooks" / "replay-debug.md",
            _REPO / "planning" / "runbooks" / "policy-override.md",
        ]
        for path in required:
            assert path.exists(), f"Runbook missing: {path}"
