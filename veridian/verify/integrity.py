"""
veridian.verify.integrity
──────────────────────────
Verifier chain integrity checker — guards against Pathway 6: Evaluation Misevolution.

DGM-H agents sabotaged their own hallucination detection code to achieve perfect
scores. This module ensures the verifier chain cannot be tampered with during a run.

Usage:
    # At run start
    checker = VerifierIntegrityChecker(registry=registry)

    # At run end
    result = checker.check()
    if not result.intact:
        raise VerifierIntegrityError(result.violations)

Checks:
├── Hash every verifier class at run start
├── Re-verify class hashes at run end — detect mid-run tampering
├── Detect added/removed/replaced verifiers
├── Immutable audit log for tracing (proves which checks were active)
└── Optional raise-on-fail mode for hard enforcement
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

from veridian.core.exceptions import VerifierIntegrityError
from veridian.verify.base import BaseVerifier, VerifierRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntegrityResult:
    """Result of a verifier integrity check."""

    intact: bool
    violations: list[str] = field(default_factory=list)


class VerifierIntegrityChecker:
    """
    Captures a cryptographic snapshot of the verifier registry at run start
    and verifies it hasn't changed at run end.

    This is a read-only checker — it never modifies the registry.
    """

    def __init__(self, registry: VerifierRegistry) -> None:
        """
        Args:
            registry: The verifier registry to monitor. A snapshot of all
                registered verifier class hashes is captured immediately.
        """
        self._registry = registry
        self._initial_snapshot: dict[str, str] = self._compute_snapshot()
        log.info(
            "verifier_integrity.snapshot ids=%s",
            sorted(self._initial_snapshot.keys()),
        )

    def snapshot(self) -> dict[str, str]:
        """Return a copy of the initial snapshot (verifier_id → hash)."""
        return dict(self._initial_snapshot)

    def check(self, raise_on_fail: bool = False) -> IntegrityResult:
        """
        Compare current registry state against the initial snapshot.

        Args:
            raise_on_fail: If True, raise VerifierIntegrityError on any violation.

        Returns:
            IntegrityResult with intact=True if no changes detected.
        """
        current = self._compute_snapshot()
        violations: list[str] = []

        # Check for removed verifiers
        for vid, original_hash in self._initial_snapshot.items():
            if vid not in current:
                violations.append(f"Verifier '{vid}' was removed during run")
            elif current[vid] != original_hash:
                violations.append(
                    f"Verifier '{vid}' was replaced — hash changed "
                    f"from {original_hash[:12]} to {current[vid][:12]}"
                )

        # Check for added verifiers
        for vid in current:
            if vid not in self._initial_snapshot:
                violations.append(f"Verifier '{vid}' was added during run")

        if violations:
            log.warning("verifier_integrity.violated violations=%s", violations)
            if raise_on_fail:
                raise VerifierIntegrityError("; ".join(violations))

        return IntegrityResult(
            intact=len(violations) == 0,
            violations=violations,
        )

    def audit_log(self) -> list[dict[str, Any]]:
        """
        Return audit entries for all verifiers in the initial snapshot.

        Each entry contains verifier_id, hash, and class_name — suitable
        for inclusion in trace events.
        """
        entries: list[dict[str, Any]] = []
        for vid, vhash in sorted(self._initial_snapshot.items()):
            cls = self._registry._classes.get(vid)
            entries.append(
                {
                    "verifier_id": vid,
                    "hash": vhash,
                    "class_name": cls.__name__ if cls else "<removed>",
                }
            )
        return entries

    def _compute_snapshot(self) -> dict[str, str]:
        """Compute hashes for all currently registered verifier classes."""
        snapshot: dict[str, str] = {}
        for vid, cls in self._registry._classes.items():
            snapshot[vid] = self._hash_class(cls)
        return snapshot

    @staticmethod
    def _hash_class(cls: type[BaseVerifier]) -> str:
        """
        Compute a stable hash for a verifier class.

        Uses class name + module + source code (if available) to detect
        replacements even when the id stays the same.
        """
        hasher = hashlib.sha256()
        hasher.update(cls.__name__.encode())
        hasher.update((cls.__module__ or "").encode())

        # Include source code if available (detects code changes)
        try:
            source = inspect.getsource(cls)
            hasher.update(source.encode())
        except (OSError, TypeError):
            # Fallback: use class qualname + id + description
            hasher.update((cls.__qualname__ or "").encode())
            hasher.update(getattr(cls, "id", "").encode())
            hasher.update(getattr(cls, "description", "").encode())

        return hasher.hexdigest()
