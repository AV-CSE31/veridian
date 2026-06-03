"""Stable verification reports and local evidence-chain utilities."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from filelock import FileLock

from veridian.core.task import Task, TaskResult

SCHEMA_VERSION = "verification-report.v1"

__all__ = [
    "SCHEMA_VERSION",
    "ReportChainValidation",
    "VerificationReport",
    "append_report_jsonl",
    "latest_report_hash",
    "stable_hash",
    "validate_report_chain",
]


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(payload: Any) -> str:
    """Return a stable SHA-256 hex digest for a JSON-like payload."""
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _task_hash_payload(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "phase": task.phase,
        "verifier_id": task.verifier_id,
        "verifier_config": task.verifier_config,
        "depends_on": task.depends_on,
        "metadata": task.metadata,
    }


def _result_hash_payload(result: TaskResult) -> dict[str, Any]:
    return {
        "raw_output": result.raw_output,
        "structured": result.structured,
        "artifacts": result.artifacts,
        "bash_outputs": result.bash_outputs,
        "token_usage": result.token_usage,
        "tool_calls": result.tool_calls,
    }


@dataclass(frozen=True)
class VerificationReport:
    """Immutable evidence record for one verifier decision.

    Reports are intentionally stdlib-shaped: dictionaries, strings, floats,
    and lists only. That makes them safe to persist in ledgers, append to JSONL,
    upload to a future evidence service, or export during audits.
    """

    report_id: str
    task_id: str
    task_title: str
    verifier_id: str
    passed: bool
    input_hash: str
    output_hash: str
    created_at: str
    runtime_version: str
    schema_version: str = SCHEMA_VERSION
    run_id: str | None = None
    verifier_version: str = "unknown"
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    previous_hash: str | None = None
    report_hash: str = ""

    @classmethod
    def from_task_result(
        cls,
        *,
        task: Task,
        result: TaskResult,
        passed: bool,
        error: str | None,
        evidence: dict[str, Any],
        score: float | None,
        runtime_version: str,
        run_id: str | None = None,
        verifier_version: str = "unknown",
        previous_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VerificationReport:
        report = cls(
            report_id=str(uuid.uuid4()),
            task_id=task.id,
            task_title=task.title,
            run_id=run_id,
            verifier_id=task.verifier_id,
            verifier_version=verifier_version,
            passed=passed,
            error=error,
            evidence=dict(evidence),
            score=score,
            input_hash=stable_hash(_task_hash_payload(task)),
            output_hash=stable_hash(_result_hash_payload(result)),
            created_at=datetime.now(tz=UTC).isoformat(),
            runtime_version=runtime_version,
            metadata=dict(metadata or {}),
            previous_hash=previous_hash,
        )
        return report.with_computed_hash()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationReport:
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            report_id=str(data.get("report_id", "")),
            task_id=str(data.get("task_id", "")),
            task_title=str(data.get("task_title", "")),
            run_id=data.get("run_id"),
            verifier_id=str(data.get("verifier_id", "")),
            verifier_version=str(data.get("verifier_version", "unknown")),
            passed=bool(data.get("passed", False)),
            error=data.get("error"),
            evidence=dict(data.get("evidence", {}) or {}),
            score=data.get("score"),
            input_hash=str(data.get("input_hash", "")),
            output_hash=str(data.get("output_hash", "")),
            created_at=str(data.get("created_at", "")),
            runtime_version=str(data.get("runtime_version", "")),
            metadata=dict(data.get("metadata", {}) or {}),
            previous_hash=data.get("previous_hash"),
            report_hash=str(data.get("report_hash", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "run_id": self.run_id,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "passed": self.passed,
            "error": self.error,
            "evidence": self.evidence,
            "score": self.score,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "created_at": self.created_at,
            "runtime_version": self.runtime_version,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "report_hash": self.report_hash,
        }

    def hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("report_hash", None)
        return payload

    def compute_hash(self) -> str:
        return stable_hash(self.hash_payload())

    def with_computed_hash(self) -> VerificationReport:
        return replace(self, report_hash=self.compute_hash())

    def with_previous_hash(self, previous_hash: str | None) -> VerificationReport:
        return replace(self, previous_hash=previous_hash, report_hash="").with_computed_hash()


@dataclass(frozen=True)
class ReportChainValidation:
    """Result returned by :func:`validate_report_chain`."""

    valid: bool
    checked_count: int
    error: str | None = None


def _lock_for(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


def latest_report_hash(path: str | Path) -> str | None:
    report_path = Path(path)
    if not report_path.exists():
        return None
    last_line = ""
    for line in report_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last_line = line
    if not last_line:
        return None
    try:
        data = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    value = data.get("report_hash")
    return str(value) if value else None


def append_report_jsonl(path: str | Path, report: VerificationReport) -> VerificationReport:
    """Append a report to a JSONL evidence chain and return the written report."""
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(report_path):
        chained = report.with_previous_hash(latest_report_hash(report_path))
        with report_path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json(chained.to_dict()) + "\n")
        return chained


def validate_report_chain(path: str | Path) -> ReportChainValidation:
    """Validate each report hash and previous-hash link in a JSONL evidence chain."""
    report_path = Path(path)
    if not report_path.exists():
        return ReportChainValidation(valid=True, checked_count=0)

    previous_hash: str | None = None
    checked = 0
    for line_number, line in enumerate(report_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            report = VerificationReport.from_dict(json.loads(line))
        except (TypeError, json.JSONDecodeError) as exc:
            return ReportChainValidation(False, checked, f"line {line_number}: invalid JSON: {exc}")
        if report.previous_hash != previous_hash:
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: previous_hash mismatch",
            )
        if report.compute_hash() != report.report_hash:
            return ReportChainValidation(False, checked, f"line {line_number}: hash mismatch")
        previous_hash = report.report_hash
        checked += 1
    return ReportChainValidation(valid=True, checked_count=checked)
