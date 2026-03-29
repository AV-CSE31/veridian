"""
veridian.eval.canary
────────────────────
Canary Task Suite — held-out safety tests to detect silent regression.

A fixed set of tasks with known-correct outputs that the agent NEVER sees
during self-improvement. Run before and after every evolution cycle.
If ANY canary fails that previously passed, evolution is BLOCKED.

Design:
  - Tasks stored as JSON with expected verifier outcomes
  - Results compared against immutable baselines
  - Hard gate: canary regression -> evolution blocked
  - Zero tolerance for regressions
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.core.exceptions import CanaryRegressionError, VeridianConfigError

__all__ = ["CanaryTask", "CanarySuite", "CanaryResult", "CanaryReport"]


@dataclass
class CanaryTask:
    """A single canary test case with expected verifier outcome."""

    task_id: str = ""
    title: str = ""
    verifier_id: str = ""
    verifier_config: dict[str, Any] = field(default_factory=dict)
    expected_pass: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "verifier_id": self.verifier_id,
            "verifier_config": self.verifier_config,
            "expected_pass": self.expected_pass,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CanaryTask:
        """Deserialize from dict."""
        return cls(
            task_id=d.get("task_id", ""),
            title=d.get("title", ""),
            verifier_id=d.get("verifier_id", ""),
            verifier_config=d.get("verifier_config", {}),
            expected_pass=d.get("expected_pass", True),
        )


class CanarySuite:
    """Collection of canary tasks. Validates integrity on construction."""

    def __init__(self, tasks: list[CanaryTask]) -> None:
        if not tasks:
            raise VeridianConfigError("Canary suite must not be empty")

        seen_ids: set[str] = set()
        for t in tasks:
            if t.task_id in seen_ids:
                raise VeridianConfigError(
                    f"Canary suite has duplicate task_id: '{t.task_id}'"
                )
            seen_ids.add(t.task_id)

        self.tasks = tasks

    @classmethod
    def from_file(cls, path: Path) -> CanarySuite:
        """Load canary suite from a JSON file."""
        data = json.loads(path.read_text())
        tasks = [CanaryTask.from_dict(d) for d in data]
        return cls(tasks=tasks)

    def save(self, path: Path) -> None:
        """Atomic write: save canary suite to JSON."""
        content = json.dumps([t.to_dict() for t in self.tasks], indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)
        os.replace(tmp_path, path)


@dataclass
class CanaryResult:
    """Result of running a single canary task."""

    task_id: str = ""
    expected_pass: bool = True
    actual_pass: bool = True
    error: str = ""

    @property
    def regression(self) -> bool:
        """True if expected pass but actually failed."""
        return self.expected_pass and not self.actual_pass

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "task_id": self.task_id,
            "expected_pass": self.expected_pass,
            "actual_pass": self.actual_pass,
            "regression": self.regression,
            "error": self.error,
        }


@dataclass
class CanaryReport:
    """Aggregated canary suite report."""

    results: list[CanaryResult] = field(default_factory=list)
    run_id: str = ""

    @property
    def regression_count(self) -> int:
        """Number of canary regressions."""
        return sum(1 for r in self.results if r.regression)

    @property
    def passed(self) -> bool:
        """True if no regressions detected."""
        return self.regression_count == 0

    @property
    def regressed_task_ids(self) -> list[str]:
        """Task IDs of regressed canaries."""
        return [r.task_id for r in self.results if r.regression]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "run_id": self.run_id,
            "total_canaries": len(self.results),
            "regression_count": self.regression_count,
            "passed": self.passed,
            "regressed_task_ids": self.regressed_task_ids,
            "results": [r.to_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        """Generate canary report markdown."""
        lines = [
            f"# Canary Report — {self.run_id}",
            "",
            f"**Total canaries:** {len(self.results)}",
            f"**Regressions:** {self.regression_count}",
            f"**Passed:** {'YES' if self.passed else 'NO'}",
            "",
        ]
        if self.regressed_task_ids:
            lines.append("## Regressions")
            lines.append("")
            lines.append("| Task ID | Expected | Actual | Regression |")
            lines.append("|---------|----------|--------|------------|")
            for r in self.results:
                if r.regression:
                    lines.append(
                        f"| {r.task_id} | pass | fail | YES |"
                    )
            lines.append("")
        else:
            lines.append("All canary tasks passed. No regressions detected.")
        lines.append("")
        return "\n".join(lines)

    def raise_on_regression(self) -> None:
        """Raise CanaryRegressionError if any regressions detected."""
        if not self.passed:
            raise CanaryRegressionError(self.regressed_task_ids)
