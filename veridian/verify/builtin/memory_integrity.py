"""
veridian.verify.builtin.memory_integrity
─────────────────────────────────────────
Validates memory/skill updates for bias, contradiction, and encoded attack patterns.

Covers Pathway 2: Memory Misevolution — 71.8% unsafe when experience is biased
(Misevolution paper).

Checks:
├── Source attribution (every entry traceable to verified task)
├── Max entry length (prevent payload injection)
├── Encoded attack pattern detection (base64+exec, prompt injection)
├── Numeric drift detection (value changed too far from previous)
├── Contradiction detection (explicit flag or value reversal)
├── Reward hacking detection (metrics up, quality indicators down)
└── No mutable default arguments (stateless verifier)
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"IGNORE\s+(ALL\s+)?(PREVIOUS\s+)?INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"YOU\s+ARE\s+NOW\s+A", re.IGNORECASE),
    re.compile(r"SYSTEM\s*:\s*", re.IGNORECASE),
    re.compile(r"<\s*/?\s*system\s*>", re.IGNORECASE),
    re.compile(r"DISREGARD\s+(ALL\s+)?PRIOR", re.IGNORECASE),
    re.compile(r"OVERRIDE\s+(SAFETY|INSTRUCTIONS|RULES)", re.IGNORECASE),
]

# Patterns for encoded payloads
_ENCODED_ATTACK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"base64\s*\.\s*b64decode", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"compile\s*\(", re.IGNORECASE),
    re.compile(r"\\x[0-9a-fA-F]{2}", re.IGNORECASE),  # hex-encoded bytes
]

# Quality indicator keys — if these go down while success metrics go up, flag reward hacking
_QUALITY_INDICATORS: frozenset[str] = frozenset(
    {
        "verification_depth",
        "test_coverage",
        "review_score",
        "safety_score",
        "quality_score",
        "compliance_score",
    }
)

_SUCCESS_METRICS: frozenset[str] = frozenset(
    {
        "success_rate",
        "completion_rate",
        "accuracy",
        "throughput",
        "speed",
        "efficiency",
    }
)


class MemoryIntegrityVerifier(BaseVerifier):
    """
    Validate memory updates for bias, contradiction, and encoded attacks.

    Operates on ``result.structured["memory_entries"]`` — a list of dicts,
    each with ``key``, ``value``, ``source_task_id``, and optional
    ``previous_value``, ``contradicts``.

    Stateless: all config via constructor. Safe for concurrent use.
    """

    id: ClassVar[str] = "memory_integrity"
    description: ClassVar[str] = (
        "Validates memory updates for source attribution, contradiction, "
        "numeric drift, encoded attacks, and reward hacking patterns."
    )

    def __init__(
        self,
        max_entry_length: int = 10_000,
        max_numeric_drift: float = 5.0,
    ) -> None:
        """
        Args:
            max_entry_length: Maximum character length per memory entry value.
            max_numeric_drift: Maximum allowed absolute change in numeric values
                relative to previous_value. E.g. 0.5 means value can't drift
                more than 0.5 from previous_value.
        """
        if max_entry_length <= 0:
            raise VeridianConfigError(
                f"MemoryIntegrityVerifier: 'max_entry_length' must be > 0, got {max_entry_length}."
            )
        if max_numeric_drift <= 0:
            raise VeridianConfigError(
                f"MemoryIntegrityVerifier: 'max_numeric_drift' must be > 0, "
                f"got {max_numeric_drift}."
            )
        self.max_entry_length = max_entry_length
        self.max_numeric_drift = max_numeric_drift

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Validate all memory entries in the task result."""
        entries = result.structured.get("memory_entries")
        if not entries or not isinstance(entries, list):
            return VerificationResult(passed=True, evidence={"checked": 0})

        all_violations: list[str] = []

        for entry in entries:
            if not isinstance(entry, dict):
                all_violations.append("Invalid memory entry: expected dict")
                continue
            violations = self._check_entry(entry)
            all_violations.extend(violations)

        # Check for reward hacking across all entries
        rh_violations = self._check_reward_hacking(entries)
        all_violations.extend(rh_violations)

        if not all_violations:
            return VerificationResult(
                passed=True,
                evidence={"checked": len(entries)},
            )

        error = self._format_error(all_violations)
        return VerificationResult(
            passed=False,
            error=error,
            evidence={"violations": all_violations, "checked": len(entries)},
        )

    def _check_entry(self, entry: dict[str, Any]) -> list[str]:
        """Run all checks on a single memory entry."""
        violations: list[str] = []
        key = entry.get("key", "<unknown>")
        value = str(entry.get("value", ""))

        # 1. Source attribution
        if "source_task_id" not in entry:
            violations.append(
                f"Entry '{key}': missing source_task_id — "
                f"every memory entry must be traceable to a verified task"
            )

        # 2. Length check
        if len(value) > self.max_entry_length:
            violations.append(
                f"Entry '{key}': value length ({len(value)}) exceeds max ({self.max_entry_length})"
            )

        # 3. Encoded attack patterns
        for pattern in _ENCODED_ATTACK_PATTERNS:
            if pattern.search(value):
                violations.append(
                    f"Entry '{key}': contains encoded attack pattern — "
                    f"memory values must not contain executable code"
                )
                break  # one match is enough

        # 4. Prompt injection patterns
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(value):
                violations.append(
                    f"Entry '{key}': contains prompt injection pattern — "
                    f"memory values must not contain instruction overrides"
                )
                break

        # 5. Numeric drift
        drift_violation = self._check_numeric_drift(entry, key)
        if drift_violation:
            violations.append(drift_violation)

        # 6. Explicit contradiction
        if entry.get("contradicts") is True:
            previous = entry.get("previous_value", "<unknown>")
            violations.append(
                f"Entry '{key}': contradicts previous value '{previous}' — "
                f"contradictions require explicit justification"
            )

        return violations

    def _check_numeric_drift(self, entry: dict[str, Any], key: str) -> str | None:
        """Check if numeric value drifted too far from previous."""
        if "previous_value" not in entry:
            return None

        try:
            current = float(entry["value"])
            previous = float(entry["previous_value"])
        except (ValueError, TypeError):
            return None  # not numeric — skip

        drift = abs(current - previous)
        if drift > self.max_numeric_drift:
            return (
                f"Entry '{key}': numeric drift {drift:.3f} exceeds max "
                f"{self.max_numeric_drift} (was {previous}, now {current})"
            )
        return None

    def _check_reward_hacking(self, entries: list[dict[str, Any]]) -> list[str]:
        """Detect reward hacking: success metrics up while quality indicators down."""
        success_up = False
        quality_down = False

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key", "")
            try:
                current = float(entry.get("value", ""))
                previous = float(entry.get("previous_value", ""))
            except (ValueError, TypeError):
                continue

            if key in _SUCCESS_METRICS and current > previous:
                success_up = True
            if key in _QUALITY_INDICATORS and current < previous:
                quality_down = True

        if success_up and quality_down:
            return [
                "Reward hacking detected: success metrics increased while "
                "quality indicators decreased — possible gaming of metrics"
            ]
        return []

    @staticmethod
    def _format_error(violations: list[str]) -> str:
        """Format violations into an actionable error message ≤ 300 chars."""
        if len(violations) == 1:
            return violations[0][:300]

        msg = f"[{len(violations)} violations] "
        msg += "; ".join(violations)
        return msg[:300]
