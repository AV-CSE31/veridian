"""
veridian.explain.engine
────────────────────────
Verification Explanation Engine.

Generates human-readable explanations for every verification decision.
Supports three detail levels: BRIEF, STANDARD, DETAILED.

Every Explanation is fully serialisable — suitable for audit trail storage.

Design constraints (CLAUDE.md):
- Stateless — ExplanationEngine has no mutable state.
- Dependency injection: no hard-coded provider calls.
- Raise from the hierarchy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from veridian.core.task import Task, TaskResult
from veridian.verify.base import VerificationResult

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────


class ExplanationDetail(StrEnum):
    """Verbosity level for an Explanation."""

    BRIEF = "brief"  # one-line summary, ≤ 150 chars
    STANDARD = "standard"  # concise paragraph with key facts
    DETAILED = "detailed"  # full breakdown with all evidence links


class EvidenceType(StrEnum):
    """Category of evidence attached to an Explanation."""

    FIELD_VALUE = "field_value"
    MISSING_FIELD = "missing_field"
    TYPE_MISMATCH = "type_mismatch"
    PATTERN_MATCH = "pattern_match"
    RANGE_VIOLATION = "range_violation"
    SCORE = "score"
    CUSTOM = "custom"


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Evidence:
    """
    A single piece of supporting evidence for an Explanation.

    type     — category of the evidence
    content  — human-readable description of what was found
    location — dotted path or location in the output where the evidence was found
    """

    type: EvidenceType
    content: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "content": self.content,
            "location": self.location,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(
            type=EvidenceType(d["type"]),
            content=d["content"],
            location=d.get("location", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Explanation:
    """
    Structured explanation for a single verification decision.

    Attributes
    ----------
    verifier_id  : str          — which verifier produced this decision
    task_id      : str          — which task was being verified
    passed       : bool         — the verification outcome
    reason       : str          — human-readable root cause / confirmation
    detail_level : ExplanationDetail — verbosity level used to generate this explanation
    evidence     : list[Evidence]    — supporting evidence items
    generated_at : str          — ISO-8601 timestamp
    """

    verifier_id: str
    task_id: str
    passed: bool
    reason: str
    detail_level: ExplanationDetail
    evidence: list[Evidence] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # ── Human-readable output ─────────────────────────────────────────────────

    def summary(self) -> str:
        """
        Return a human-readable summary appropriate for the detail level.
        """
        status = "PASSED" if self.passed else "FAILED"

        if self.detail_level == ExplanationDetail.BRIEF:
            return f"[{status}] {self.verifier_id} on task {self.task_id}: {self.reason}"

        lines = [
            f"Verification {status}",
            f"  Verifier : {self.verifier_id}",
            f"  Task     : {self.task_id}",
            f"  Reason   : {self.reason}",
        ]

        if self.detail_level == ExplanationDetail.DETAILED and self.evidence:
            lines.append("  Evidence :")
            for ev in self.evidence:
                loc = f" (at {ev.location})" if ev.location else ""
                lines.append(f"    - [{ev.type.value}] {ev.content}{loc}")

        return "\n".join(lines)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "task_id": self.task_id,
            "passed": self.passed,
            "reason": self.reason,
            "detail_level": self.detail_level.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Explanation:
        return cls(
            verifier_id=d["verifier_id"],
            task_id=d["task_id"],
            passed=d["passed"],
            reason=d["reason"],
            detail_level=ExplanationDetail(d["detail_level"]),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
            generated_at=d.get("generated_at", datetime.now(UTC).isoformat()),
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────


class ExplanationEngine:
    """
    Stateless engine that converts VerificationResult objects into Explanation
    objects at the requested detail level.

    Usage:
        engine = ExplanationEngine()
        exp = engine.explain(result, task, task_result, verifier_id, detail=ExplanationDetail.STANDARD)
        print(exp.summary())
    """

    def explain(
        self,
        result: VerificationResult,
        task: Task,
        task_result: TaskResult,
        verifier_id: str,
        detail: ExplanationDetail = ExplanationDetail.STANDARD,
    ) -> Explanation:
        """
        Generate an Explanation for a VerificationResult.

        Parameters
        ----------
        result      : VerificationResult — the outcome to explain
        task        : Task               — the task that was verified
        task_result : TaskResult         — the result that was verified
        verifier_id : str                — id of the verifier that produced result
        detail      : ExplanationDetail  — verbosity level
        """
        evidence = self._extract_evidence(result, task_result, detail)
        reason = self._build_reason(result, task, task_result, verifier_id, detail)

        exp = Explanation(
            verifier_id=verifier_id,
            task_id=task.id,
            passed=result.passed,
            reason=reason,
            detail_level=detail,
            evidence=evidence,
        )
        log.debug(
            "explain.generate verifier=%s task=%s passed=%s detail=%s",
            verifier_id,
            task.id,
            result.passed,
            detail.value,
        )
        return exp

    def batch_explain(
        self,
        items: list[tuple[VerificationResult, Task, TaskResult, str]],
        detail: ExplanationDetail = ExplanationDetail.STANDARD,
    ) -> list[Explanation]:
        """
        Explain multiple results at once.

        items — list of (result, task, task_result, verifier_id) tuples.
        """
        return [
            self.explain(result, task, task_result, verifier_id, detail)
            for result, task, task_result, verifier_id in items
        ]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_reason(
        self,
        result: VerificationResult,
        task: Task,
        task_result: TaskResult,
        verifier_id: str,
        detail: ExplanationDetail,
    ) -> str:
        """Build a human-readable reason string appropriate for the detail level."""
        if result.passed:
            base = f"Verification passed by '{verifier_id}'"
            if result.score is not None and detail != ExplanationDetail.BRIEF:
                base += f" with score {result.score:.2f}"
            return base

        # Failed
        error = result.error or "Verification failed — no specific error provided."
        if detail == ExplanationDetail.BRIEF:
            # Truncate to keep brief
            return error[:150] if len(error) > 150 else error

        parts = [f"This output was flagged because: {error}"]

        if result.score is not None:
            parts.append(f"Score: {result.score:.2f}")

        if detail == ExplanationDetail.DETAILED:
            parts.append(f"Task: '{task.title}' ({task.id})")

        return " | ".join(parts)

    def _extract_evidence(
        self,
        result: VerificationResult,
        task_result: TaskResult,
        detail: ExplanationDetail,
    ) -> list[Evidence]:
        """Extract structured evidence from a VerificationResult."""
        evidence: list[Evidence] = []

        if detail == ExplanationDetail.BRIEF:
            return evidence  # Brief level: no evidence items

        # Score evidence
        if result.score is not None:
            evidence.append(
                Evidence(
                    type=EvidenceType.SCORE,
                    content=f"Verification score: {result.score:.2f}",
                    location="result.score",
                )
            )

        if detail == ExplanationDetail.STANDARD:
            return evidence

        # Detailed level: mine the evidence dict for structured items
        for key, value in result.evidence.items():
            if key == "missing_fields" and isinstance(value, list):
                for f in value:
                    evidence.append(
                        Evidence(
                            type=EvidenceType.MISSING_FIELD,
                            content=f"Required field '{f}' was not present",
                            location=f"output.{f}",
                        )
                    )
            elif key == "provided_fields" and isinstance(value, list):
                pass  # informational — already covered by missing_fields
            elif key == "type_mismatches" and isinstance(value, list):
                for item in value:
                    evidence.append(
                        Evidence(
                            type=EvidenceType.TYPE_MISMATCH,
                            content=str(item),
                            location="output",
                        )
                    )
            elif key == "pattern" and isinstance(value, str):
                evidence.append(
                    Evidence(
                        type=EvidenceType.PATTERN_MATCH,
                        content=f"Pattern check: {value}",
                        location="output",
                    )
                )
            else:
                evidence.append(
                    Evidence(
                        type=EvidenceType.CUSTOM,
                        content=f"{key}: {value!r}",
                        location="result.evidence",
                    )
                )

        return evidence
