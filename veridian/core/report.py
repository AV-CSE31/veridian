"""Stable verification reports and local evidence-chain utilities."""

from __future__ import annotations

import hmac
import json
import os
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
    "sign_report",
    "stable_hash",
    "validate_report_chain",
]


SIGNATURE_VERSION = "hmac-sha256.v1"
_DEFAULT_SIGNING_KEY = "veridian-local-report-signing-key-v1"


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(payload: Any) -> str:
    """Return a stable SHA-256 hex digest for a JSON-like payload."""
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _signing_key() -> str:
    return os.getenv("VERIDIAN_REPORT_SIGNING_KEY", _DEFAULT_SIGNING_KEY)


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
    input_payload: dict[str, Any] = field(default_factory=dict)
    output_payload: dict[str, Any] = field(default_factory=dict)
    task: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    previous_hash: str | None = None
    signing_key_id: str = "local"
    signature_version: str = SIGNATURE_VERSION
    signature: str = ""
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
            input_payload=_task_hash_payload(task),
            output_payload=_result_hash_payload(result),
            task=_task_hash_payload(task),
            result=_result_hash_payload(result),
            created_at=datetime.now(tz=UTC).isoformat(),
            runtime_version=runtime_version,
            metadata=dict(metadata or {}),
            previous_hash=previous_hash,
        )
        return report.with_computed_hash()

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> VerificationReport:
        if strict:
            extra = sorted(set(data) - _ALLOWED_REPORT_FIELDS)
            if extra:
                raise ValueError(f"unknown report field(s): {', '.join(extra)}")
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
            input_payload=dict(data.get("input_payload", {}) or {}),
            output_payload=dict(data.get("output_payload", {}) or {}),
            task=dict(data.get("task", data.get("input_payload", {})) or {}),
            result=dict(data.get("result", data.get("output_payload", {})) or {}),
            created_at=str(data.get("created_at", "")),
            runtime_version=str(data.get("runtime_version", "")),
            metadata=dict(data.get("metadata", {}) or {}),
            previous_hash=data.get("previous_hash"),
            signing_key_id=str(data.get("signing_key_id", "local")),
            signature_version=str(data.get("signature_version", SIGNATURE_VERSION)),
            signature=str(data.get("signature", "")),
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
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "task": self.task,
            "result": self.result,
            "previous_hash": self.previous_hash,
            "signing_key_id": self.signing_key_id,
            "signature_version": self.signature_version,
            "signature": self.signature,
            "report_hash": self.report_hash,
        }

    def hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("report_hash", None)
        payload.pop("signature", None)
        return payload

    def compute_hash(self) -> str:
        return stable_hash(self.hash_payload())

    def with_computed_hash(self) -> VerificationReport:
        return replace(self, report_hash=self.compute_hash())

    def with_previous_hash(self, previous_hash: str | None) -> VerificationReport:
        return replace(
            self, previous_hash=previous_hash, report_hash="", signature=""
        ).with_computed_hash()


_ALLOWED_REPORT_FIELDS = frozenset(
    VerificationReport(
        report_id="",
        task_id="",
        task_title="",
        verifier_id="",
        passed=False,
        input_hash="",
        output_hash="",
        created_at="",
        runtime_version="",
    ).to_dict()
)


@dataclass(frozen=True)
class ReportChainValidation:
    """Result returned by :func:`validate_report_chain`."""

    valid: bool
    checked_count: int
    error: str | None = None


def _lock_for(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


def sign_report(report: VerificationReport, signing_key: str | None = None) -> VerificationReport:
    """Return a report with an HMAC signature over the computed report hash."""
    prepared = replace(
        report,
        signing_key_id="env"
        if signing_key or os.getenv("VERIDIAN_REPORT_SIGNING_KEY")
        else "local",
        signature_version=SIGNATURE_VERSION,
        signature="",
    )
    hashed = prepared.with_computed_hash()
    signature = hmac.new(
        (signing_key or _signing_key()).encode("utf-8"),
        hashed.report_hash.encode("utf-8"),
        sha256,
    ).hexdigest()
    return replace(hashed, signature=signature)


def _signature_valid(report: VerificationReport, signing_key: str | None = None) -> bool:
    if report.signature_version != SIGNATURE_VERSION or not report.signature:
        return False
    expected = hmac.new(
        (signing_key or _signing_key()).encode("utf-8"),
        report.report_hash.encode("utf-8"),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, report.signature)


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
        chained = sign_report(report.with_previous_hash(latest_report_hash(report_path)))
        with report_path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json(chained.to_dict()) + "\n")
        return chained


def validate_report_chain(
    path: str | Path,
    *,
    require_signature: bool = True,
    signing_key: str | None = None,
) -> ReportChainValidation:
    """Validate each report hash and previous-hash link in a JSONL evidence chain."""
    report_path = Path(path)
    if not report_path.exists():
        return ReportChainValidation(valid=False, checked_count=0, error="evidence chain missing")

    previous_hash: str | None = None
    checked = 0
    lines = report_path.read_text(encoding="utf-8").splitlines()
    if not any(line.strip() for line in lines):
        return ReportChainValidation(valid=False, checked_count=0, error="empty evidence chain")

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            report = VerificationReport.from_dict(json.loads(line), strict=True)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return ReportChainValidation(False, checked, f"line {line_number}: invalid JSON: {exc}")
        if report.previous_hash != previous_hash:
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: previous_hash mismatch",
            )
        if report.compute_hash() != report.report_hash:
            return ReportChainValidation(False, checked, f"line {line_number}: hash mismatch")
        if report.input_payload and stable_hash(report.input_payload) != report.input_hash:
            return ReportChainValidation(False, checked, f"line {line_number}: input hash mismatch")
        if report.output_payload and stable_hash(report.output_payload) != report.output_hash:
            return ReportChainValidation(
                False, checked, f"line {line_number}: output hash mismatch"
            )
        if require_signature and not _signature_valid(report, signing_key):
            return ReportChainValidation(False, checked, f"line {line_number}: signature mismatch")
        previous_hash = report.report_hash
        checked += 1
    return ReportChainValidation(valid=True, checked_count=checked)
