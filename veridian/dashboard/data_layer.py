"""
veridian.dashboard.data_layer
───────────────────────────────
Compliance Dashboard Data Layer (F2.4).

Aggregates verification results for monitoring, reporting, and compliance.
Supports time-series metrics, per-verifier stats, per-agent stats, and
export to JSON and CSV.

All data is held in-memory. For persistent dashboards, call export_json()
and reload from JSON, or integrate with a storage backend.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = [
    "VerificationRecord",
    "TimeSeriesPoint",
    "VerifierStats",
    "AgentStats",
    "ComplianceDashboard",
]


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VerificationRecord:
    """A single verification event captured from the pipeline."""

    task_id: str
    verifier_id: str
    agent_id: str | None
    timestamp: datetime
    passed: bool
    error: str | None = None
    response_time_ms: float = 0.0
    failure_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verifier_id": self.verifier_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "passed": self.passed,
            "error": self.error,
            "response_time_ms": self.response_time_ms,
            "failure_category": self.failure_category,
        }


@dataclass
class TimeSeriesPoint:
    """Aggregated metrics for a single time bucket."""

    timestamp: datetime
    pass_rate: float
    total_checks: int
    failures: int
    avg_response_time_ms: float = 0.0
    failure_categories: dict[str, int] = field(default_factory=dict)


@dataclass
class VerifierStats:
    """Aggregated stats for a single verifier."""

    verifier_id: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    avg_response_time_ms: float


@dataclass
class AgentStats:
    """Aggregated stats for a single agent."""

    agent_id: str
    total_tasks: int
    passed: int
    failed: int
    pass_rate: float


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


class ComplianceDashboard:
    """
    Aggregation engine for verification results.

    Stores records in-memory and provides time-series metrics, per-verifier
    statistics, per-agent statistics, and export in JSON/CSV formats.

    Usage::

        dash = ComplianceDashboard()
        dash.add_record(VerificationRecord(...))
        print(f"Pass rate: {dash.pass_rate():.1%}")
        print(dash.generate_summary_report())
    """

    def __init__(self) -> None:
        self._records: list[VerificationRecord] = []

    @property
    def total_records(self) -> int:
        return len(self._records)

    def add_record(self, record: VerificationRecord) -> None:
        """Append a verification record to the dashboard."""
        self._records.append(record)

    # ── Core metrics ──────────────────────────────────────────────────────────

    def pass_rate(self, *, since: datetime | None = None) -> float:
        """
        Overall pass rate across all records.

        Parameters
        ----------
        since : Optional datetime filter — only records at or after this timestamp.
        """
        records = self._filter_since(since)
        if not records:
            return 0.0
        return sum(1 for r in records if r.passed) / len(records)

    def failure_categories(self, *, since: datetime | None = None) -> dict[str, int]:
        """Return a count dict of failure_category → number of failures."""
        records = self._filter_since(since)
        cats: dict[str, int] = {}
        for r in records:
            if not r.passed and r.failure_category:
                cats[r.failure_category] = cats.get(r.failure_category, 0) + 1
        return cats

    # ── Time-series ───────────────────────────────────────────────────────────

    def time_series(
        self,
        bucket_size: timedelta = timedelta(hours=1),
        *,
        since: datetime | None = None,
    ) -> list[TimeSeriesPoint]:
        """
        Aggregate records into time buckets.

        Returns one TimeSeriesPoint per non-empty bucket, ordered by time.
        """
        records = self._filter_since(since)
        if not records:
            return []

        # Determine bucket boundaries
        buckets: dict[datetime, list[VerificationRecord]] = {}
        for rec in records:
            bucket_key = self._bucket_floor(rec.timestamp, bucket_size)
            buckets.setdefault(bucket_key, []).append(rec)

        points = []
        for ts in sorted(buckets):
            bucket_recs = buckets[ts]
            total = len(bucket_recs)
            passed = sum(1 for r in bucket_recs if r.passed)
            failures = total - passed
            avg_rt = (
                sum(r.response_time_ms for r in bucket_recs) / total if total else 0.0
            )
            cats: dict[str, int] = {}
            for r in bucket_recs:
                if not r.passed and r.failure_category:
                    cats[r.failure_category] = cats.get(r.failure_category, 0) + 1
            points.append(
                TimeSeriesPoint(
                    timestamp=ts,
                    pass_rate=passed / total,
                    total_checks=total,
                    failures=failures,
                    avg_response_time_ms=avg_rt,
                    failure_categories=cats,
                )
            )
        return points

    # ── Per-verifier stats ────────────────────────────────────────────────────

    def per_verifier_stats(self) -> dict[str, VerifierStats]:
        """Return VerifierStats keyed by verifier_id."""
        grouped: dict[str, list[VerificationRecord]] = {}
        for rec in self._records:
            grouped.setdefault(rec.verifier_id, []).append(rec)

        result: dict[str, VerifierStats] = {}
        for vid, recs in grouped.items():
            total = len(recs)
            passed = sum(1 for r in recs if r.passed)
            avg_rt = sum(r.response_time_ms for r in recs) / total if total else 0.0
            result[vid] = VerifierStats(
                verifier_id=vid,
                total=total,
                passed=passed,
                failed=total - passed,
                pass_rate=passed / total if total else 0.0,
                avg_response_time_ms=avg_rt,
            )
        return result

    # ── Per-agent stats ───────────────────────────────────────────────────────

    def per_agent_stats(self) -> dict[str, AgentStats]:
        """Return AgentStats keyed by agent_id. Skips records with agent_id=None."""
        grouped: dict[str, list[VerificationRecord]] = {}
        for rec in self._records:
            if rec.agent_id is not None:
                grouped.setdefault(rec.agent_id, []).append(rec)

        result: dict[str, AgentStats] = {}
        for aid, recs in grouped.items():
            total = len(recs)
            passed = sum(1 for r in recs if r.passed)
            result[aid] = AgentStats(
                agent_id=aid,
                total_tasks=total,
                passed=passed,
                failed=total - passed,
                pass_rate=passed / total if total else 0.0,
            )
        return result

    # ── Export formats ────────────────────────────────────────────────────────

    def export_json(self) -> str:
        """
        Export all records and a summary to a JSON string.

        Schema: {"records": [...], "summary": {...}}
        """
        summary = self._build_summary()
        data = {
            "records": [r.to_dict() for r in self._records],
            "summary": summary,
        }
        return json.dumps(data, indent=2, default=str)

    def export_csv(self) -> str:
        """Export all records as CSV with a header row."""
        fieldnames = [
            "task_id", "verifier_id", "agent_id", "timestamp",
            "passed", "error", "response_time_ms", "failure_category",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for rec in self._records:
            writer.writerow(rec.to_dict())
        return output.getvalue()

    # ── Summary report ────────────────────────────────────────────────────────

    def generate_summary_report(self) -> str:
        """
        Generate a human-readable plain-text summary report.
        """
        total = self.total_records
        if total == 0:
            return (
                "Veridian Compliance Dashboard — Summary\n"
                "═" * 40 + "\n"
                "Total records: 0\n"
                "No verification data recorded yet.\n"
            )

        rate = self.pass_rate()
        cats = self.failure_categories()
        verifier_stats = self.per_verifier_stats()
        agent_stats = self.per_agent_stats()

        lines = [
            "Veridian Compliance Dashboard — Summary",
            "═" * 40,
            f"Total records : {total}",
            f"Pass rate     : {rate:.1%} ({int(rate * total)}/{total})",
            "",
            "─ Verifier Breakdown ─",
        ]
        for vid, vs in sorted(verifier_stats.items()):
            lines.append(
                f"  {vid:<30} pass={vs.pass_rate:.0%}  "
                f"total={vs.total}  failed={vs.failed}"
            )

        if agent_stats:
            lines += ["", "─ Agent Breakdown ─"]
            for aid, ag in sorted(agent_stats.items()):
                lines.append(
                    f"  {aid:<30} pass={ag.pass_rate:.0%}  "
                    f"total={ag.total_tasks}  failed={ag.failed}"
                )

        if cats:
            lines += ["", "─ Failure Categories ─"]
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                lines.append(f"  {cat:<30} {count}")

        return "\n".join(lines) + "\n"

    # ── Private helpers ───────────────────────────────────────────────────────

    def _filter_since(self, since: datetime | None) -> list[VerificationRecord]:
        if since is None:
            return self._records
        return [r for r in self._records if r.timestamp >= since]

    @staticmethod
    def _bucket_floor(ts: datetime, bucket_size: timedelta) -> datetime:
        """Round ts down to the nearest bucket boundary."""
        epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
        if ts.tzinfo is None:
            epoch = datetime(2000, 1, 1)
        delta = ts - epoch
        bucket_secs = int(bucket_size.total_seconds())
        floored_secs = (int(delta.total_seconds()) // bucket_secs) * bucket_secs
        return epoch + timedelta(seconds=floored_secs)

    def _build_summary(self) -> dict[str, Any]:
        total = self.total_records
        return {
            "total_records": total,
            "pass_rate": self.pass_rate(),
            "failure_categories": self.failure_categories(),
            "verifier_count": len(self.per_verifier_stats()),
            "agent_count": len(self.per_agent_stats()),
        }
