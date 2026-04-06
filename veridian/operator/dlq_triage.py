"""
veridian.operator.dlq_triage
──────────────────────────────
DLQ triage view — categorizes dead-letter entries by failure pattern.

Groups DLQ entries by common error signature so operators can triage
bulk failures efficiently instead of inspecting entries one-by-one.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FailureCategory",
    "DLQTriageView",
]

log = logging.getLogger(__name__)


@dataclass
class FailureCategory:
    """A group of DLQ entries sharing the same failure pattern."""

    category: str
    count: int
    sample_task_ids: list[str] = field(default_factory=list)
    common_error: str = ""


class DLQTriageView:
    """Categorize and report on DLQ entries for operator triage.

    Groups entries by their ``error`` field and produces summary categories
    with sample task IDs and counts.
    """

    def categorize(self, entries: list[dict[str, Any]]) -> list[FailureCategory]:
        """Group DLQ entries by error pattern.

        Args:
            entries: List of dicts with at least ``task_id`` and ``error`` keys.

        Returns:
            Sorted list of FailureCategory (highest count first).
        """
        if not entries:
            return []

        groups: dict[str, list[str]] = defaultdict(list)
        for entry in entries:
            error = str(entry.get("error", "unknown"))
            task_id = str(entry.get("task_id", ""))
            groups[error].append(task_id)

        categories: list[FailureCategory] = []
        for error, task_ids in groups.items():
            categories.append(
                FailureCategory(
                    category=error,
                    count=len(task_ids),
                    sample_task_ids=task_ids[:5],  # cap sample at 5
                    common_error=error,
                )
            )

        # Sort by count descending for operator convenience
        categories.sort(key=lambda c: c.count, reverse=True)
        return categories

    def export_report(self, categories: list[FailureCategory]) -> dict[str, Any]:
        """Export triage categories as a JSON-serializable report dict.

        Args:
            categories: List of FailureCategory from :meth:`categorize`.

        Returns:
            Dict with ``categories`` key containing serialized category data.
        """
        return {
            "categories": [
                {
                    "category": c.category,
                    "count": c.count,
                    "sample_task_ids": c.sample_task_ids,
                    "common_error": c.common_error,
                }
                for c in categories
            ],
        }
