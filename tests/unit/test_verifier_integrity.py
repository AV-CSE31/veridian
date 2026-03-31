"""
tests.unit.test_verifier_integrity
────────────────────────────────────
VerifierIntegrityChecker — guards against Pathway 6: Evaluation Misevolution.

DGM-H agents sabotaged their own hallucination detection code to achieve
perfect scores. This module ensures the verifier chain cannot be tampered
with during a run.
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import VerifierIntegrityError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult, VerifierRegistry
from veridian.verify.integrity import VerifierIntegrityChecker

# ── Helpers ───────────────────────────────────────────────────────────────────


class StubVerifierA(BaseVerifier):
    id = "stub_a"
    description = "Stub A for integrity tests"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class StubVerifierB(BaseVerifier):
    id = "stub_b"
    description = "Stub B for integrity tests"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


# ── Init / Config ─────────────────────────────────────────────────────────────


class TestVerifierIntegrityInit:
    """Construction and snapshotting."""

    def test_snapshot_on_init(self) -> None:
        """Should capture hashes of all registered verifiers at creation."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        checker = VerifierIntegrityChecker(registry=registry)
        snapshot = checker.snapshot()

        assert "stub_a" in snapshot
        assert "stub_b" in snapshot
        assert len(snapshot) == 2

    def test_snapshot_is_immutable_copy(self) -> None:
        """Snapshot returned should be a copy — modifying it doesn't affect checker."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)

        checker = VerifierIntegrityChecker(registry=registry)
        snap1 = checker.snapshot()
        snap1["stub_a"] = "tampered"

        snap2 = checker.snapshot()
        assert snap2["stub_a"] != "tampered"

    def test_empty_registry_allowed(self) -> None:
        """Should handle empty registry without error."""
        registry = VerifierRegistry()
        checker = VerifierIntegrityChecker(registry=registry)
        assert checker.snapshot() == {}


# ── Verify Integrity ─────────────────────────────────────────────────────────


class TestVerifierIntegrityCheck:
    """Core integrity verification logic."""

    def test_passes_when_unchanged(self) -> None:
        """Should pass when registry hasn't changed since snapshot."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        checker = VerifierIntegrityChecker(registry=registry)
        # Nothing changed — should pass
        result = checker.check()
        assert result.intact is True
        assert result.violations == []

    def test_detects_removed_verifier(self) -> None:
        """Should detect when a verifier is removed from registry."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        checker = VerifierIntegrityChecker(registry=registry)

        # Simulate removal by creating a new registry without stub_b
        registry._classes.pop("stub_b")

        result = checker.check()
        assert result.intact is False
        assert any("stub_b" in v for v in result.violations)
        assert any("removed" in v.lower() for v in result.violations)

    def test_detects_added_verifier(self) -> None:
        """Should detect when a new verifier is added mid-run (config changed)."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)

        checker = VerifierIntegrityChecker(registry=registry)

        # Add a verifier after snapshot
        registry.register(StubVerifierB)

        result = checker.check()
        assert result.intact is False
        assert any("stub_b" in v for v in result.violations)
        assert any("added" in v.lower() for v in result.violations)

    def test_detects_replaced_verifier(self) -> None:
        """Should detect when a verifier class is swapped for a different one."""

        class FakeStubA(BaseVerifier):
            id = "stub_a"
            description = "FAKE replacement"

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                return VerificationResult(passed=True)  # always passes — sabotage

        registry = VerifierRegistry()
        registry.register(StubVerifierA)

        checker = VerifierIntegrityChecker(registry=registry)

        # Replace with fake
        registry._classes["stub_a"] = FakeStubA

        result = checker.check()
        assert result.intact is False
        assert any("stub_a" in v for v in result.violations)

    def test_check_raises_on_failure(self) -> None:
        """check(raise_on_fail=True) should raise VerifierIntegrityError."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)

        checker = VerifierIntegrityChecker(registry=registry)
        registry._classes.pop("stub_a")

        with pytest.raises(VerifierIntegrityError, match="stub_a"):
            checker.check(raise_on_fail=True)


# ── Hashing ──────────────────────────────────────────────────────────────────


class TestVerifierHashing:
    """Hash stability and collision resistance."""

    def test_same_class_same_hash(self) -> None:
        """Same verifier class should produce the same hash."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)

        checker = VerifierIntegrityChecker(registry=registry)
        snap1 = checker.snapshot()

        checker2 = VerifierIntegrityChecker(registry=registry)
        snap2 = checker2.snapshot()

        assert snap1["stub_a"] == snap2["stub_a"]

    def test_different_classes_different_hashes(self) -> None:
        """Different verifier classes should have different hashes."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        checker = VerifierIntegrityChecker(registry=registry)
        snap = checker.snapshot()

        assert snap["stub_a"] != snap["stub_b"]


# ── Audit Trail ──────────────────────────────────────────────────────────────


class TestVerifierIntegrityAudit:
    """Audit log generation for tracing."""

    def test_audit_log_contains_all_verifiers(self) -> None:
        """audit_log() should list all verifier IDs and hashes."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        checker = VerifierIntegrityChecker(registry=registry)
        log = checker.audit_log()

        assert len(log) == 2
        ids = {entry["verifier_id"] for entry in log}
        assert ids == {"stub_a", "stub_b"}
        for entry in log:
            assert "hash" in entry
            assert "class_name" in entry

    def test_audit_log_on_empty_registry(self) -> None:
        """Should return empty list for empty registry."""
        registry = VerifierRegistry()
        checker = VerifierIntegrityChecker(registry=registry)
        assert checker.audit_log() == []


# ── Integration-style ────────────────────────────────────────────────────────


class TestVerifierIntegrityFullCycle:
    """Full run-start → run-end cycle."""

    def test_full_cycle_no_tampering(self) -> None:
        """Snapshot at start, check at end — should pass when nothing changed."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        # Run start
        checker = VerifierIntegrityChecker(registry=registry)
        start_snap = checker.snapshot()

        # ... tasks execute ...

        # Run end
        result = checker.check()
        assert result.intact is True
        end_snap = checker.snapshot()
        assert start_snap == end_snap

    def test_full_cycle_with_tampering(self) -> None:
        """Should detect tampering that occurred during run."""
        registry = VerifierRegistry()
        registry.register(StubVerifierA)
        registry.register(StubVerifierB)

        # Run start
        checker = VerifierIntegrityChecker(registry=registry)

        # Mid-run: agent sabotages verifier
        registry._classes.pop("stub_b")

        # Run end
        result = checker.check()
        assert result.intact is False
        assert len(result.violations) >= 1
