"""Stable verification reports and a conservative local evidence chain.

The JSONL chain provides signed integrity and ordering. It does not, by itself,
prove when a report existed or prevent a holder of the signing key from
rewriting history. Callers that need rollback protection must retain a trusted
head outside the report file and pass it to :func:`validate_report_chain`.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from veridian.core.exceptions import VerificationError
from veridian.core.task import Task, TaskResult

SCHEMA_VERSION = "verification-report.v2"
LEGACY_SCHEMA_VERSION = "verification-report.v1"
SIGNATURE_VERSION = "hmac-sha256.v1"
MIN_SIGNING_KEY_BYTES = 32

_KEY_HOLDER_LIMITATION = (
    "HMAC proves possession of a shared secret, not signer identity or non-repudiation; "
    "every key holder can author reports."
)
_UNANCHORED_LIMITATION = (
    "The chain head was not compared with an independently retained trusted head; "
    "rollback, truncation, freshness, and existence are not proven."
)
_COMMITMENT_LIMITATION = (
    "SHA-256 commitments conceal raw payloads but do not prevent guessing attacks "
    "against low-entropy values."
)
_LEGACY_UNSIGNED_LIMITATION = (
    "Legacy verification-report.v1 records are unsigned; validation proves only "
    "their internal SHA-256 hash chain, not who authored them."
)

__all__ = [
    "MIN_SIGNING_KEY_BYTES",
    "LEGACY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SIGNATURE_VERSION",
    "ReportChainValidation",
    "VerificationReport",
    "append_report_jsonl",
    "latest_report_hash",
    "sign_report",
    "stable_hash",
    "validate_report_chain",
]


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
        allow_nan=False,
    )


def stable_hash(payload: Any) -> str:
    """Return a stable SHA-256 commitment for a JSON-like payload."""
    try:
        canonical = _canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"payload is not canonical JSON: {exc}") from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


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
    """Immutable verifier decision with payload commitments.

    Raw task/result payloads are redacted by default. ``input_hash`` and
    ``output_hash`` remain independently checkable when an auditor receives the
    original payload through a separately controlled disclosure channel.
    ``include_payloads=True`` embeds those payloads and accepts the associated
    confidentiality risk explicitly. Verifier evidence and metadata have
    separate disclosure flags because either may also contain secrets or PII.
    """

    report_id: str
    task_id: str
    task_title: str
    verifier_id: str
    passed: bool
    input_hash: str
    output_hash: str
    evidence_hash: str
    metadata_hash: str
    payloads_disclosed: bool
    evidence_disclosed: bool
    metadata_disclosed: bool
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
    signing_key_id: str = ""
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
        include_payloads: bool = False,
        include_evidence: bool = False,
        include_metadata: bool = False,
    ) -> VerificationReport:
        input_commitment = _task_hash_payload(task)
        output_commitment = _result_hash_payload(result)
        evidence_commitment = dict(evidence)
        metadata_commitment = dict(metadata or {})
        disclosed_input = input_commitment if include_payloads else {}
        disclosed_output = output_commitment if include_payloads else {}
        report = cls(
            report_id=str(uuid.uuid4()),
            task_id=task.id,
            task_title=task.title if include_payloads else "",
            run_id=run_id,
            verifier_id=task.verifier_id,
            verifier_version=verifier_version,
            passed=passed,
            error=error,
            evidence=evidence_commitment if include_evidence else {},
            score=score,
            input_hash=stable_hash(input_commitment),
            output_hash=stable_hash(output_commitment),
            evidence_hash=stable_hash(evidence_commitment),
            metadata_hash=stable_hash(metadata_commitment),
            payloads_disclosed=include_payloads,
            evidence_disclosed=include_evidence,
            metadata_disclosed=include_metadata,
            input_payload=disclosed_input,
            output_payload=disclosed_output,
            task=disclosed_input,
            result=disclosed_output,
            created_at=datetime.now(tz=UTC).isoformat(),
            runtime_version=runtime_version,
            metadata=metadata_commitment if include_metadata else {},
            previous_hash=previous_hash,
        )
        return report.with_computed_hash()

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> VerificationReport:
        """Build a report; strict mode rejects missing, unknown, or ill-typed fields."""
        if strict:
            try:
                _validate_report_fields(data)
            except (TypeError, ValueError) as exc:
                raise VerificationError(f"invalid verification report: {exc}") from exc
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            report_id=str(data.get("report_id", "")),
            task_id=str(data.get("task_id", "")),
            task_title=str(data.get("task_title", "")),
            run_id=data.get("run_id"),
            verifier_id=str(data.get("verifier_id", "")),
            verifier_version=str(data.get("verifier_version", "unknown")),
            passed=data.get("passed", False) if strict else bool(data.get("passed", False)),
            error=data.get("error"),
            evidence=dict(data.get("evidence", {}) or {}),
            score=data.get("score"),
            input_hash=str(data.get("input_hash", "")),
            output_hash=str(data.get("output_hash", "")),
            evidence_hash=str(data.get("evidence_hash", "")),
            metadata_hash=str(data.get("metadata_hash", "")),
            payloads_disclosed=data.get("payloads_disclosed", False),
            evidence_disclosed=data.get("evidence_disclosed", False),
            metadata_disclosed=data.get("metadata_disclosed", False),
            input_payload=dict(data.get("input_payload", {}) or {}),
            output_payload=dict(data.get("output_payload", {}) or {}),
            task=dict(data.get("task", data.get("input_payload", {})) or {}),
            result=dict(data.get("result", data.get("output_payload", {})) or {}),
            created_at=str(data.get("created_at", "")),
            runtime_version=str(data.get("runtime_version", "")),
            metadata=dict(data.get("metadata", {}) or {}),
            previous_hash=data.get("previous_hash"),
            signing_key_id=str(data.get("signing_key_id", "")),
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
            "evidence_hash": self.evidence_hash,
            "metadata_hash": self.metadata_hash,
            "payloads_disclosed": self.payloads_disclosed,
            "evidence_disclosed": self.evidence_disclosed,
            "metadata_disclosed": self.metadata_disclosed,
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
            self,
            previous_hash=previous_hash,
            report_hash="",
            signature="",
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
        evidence_hash="",
        metadata_hash="",
        payloads_disclosed=False,
        evidence_disclosed=False,
        metadata_disclosed=False,
        created_at="",
        runtime_version="",
    ).to_dict()
)


@dataclass(frozen=True)
class ReportChainValidation:
    """Strict chain-validation result and its explicit trust limitations."""

    valid: bool
    checked_count: int
    error: str | None = None
    anchored: bool = False
    head_hash: str | None = None
    limitations: tuple[str, ...] = (
        _KEY_HOLDER_LIMITATION,
        _UNANCHORED_LIMITATION,
        _COMMITMENT_LIMITATION,
    )
    legacy_unsigned_count: int = 0


_LEGACY_V1_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "task_id",
        "task_title",
        "run_id",
        "verifier_id",
        "verifier_version",
        "passed",
        "error",
        "evidence",
        "score",
        "input_hash",
        "output_hash",
        "created_at",
        "runtime_version",
        "metadata",
        "previous_hash",
        "report_hash",
    }
)


def _require_string(data: dict[str, Any], name: str, *, allow_empty: bool = False) -> str:
    value = data[name]
    if not isinstance(value, str) or (not allow_empty and not value):
        suffix = "a string" if allow_empty else "a non-empty string"
        raise ValueError(f"{name} must be {suffix}")
    return value


def _require_optional_string(data: dict[str, Any], name: str) -> str | None:
    value = data[name]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _require_dict(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data[name]
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_report_fields(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("report must be an object")
    missing = sorted(_ALLOWED_REPORT_FIELDS - set(data))
    if missing:
        raise ValueError(f"missing report field(s): {', '.join(missing)}")
    extra = sorted(set(data) - _ALLOWED_REPORT_FIELDS)
    if extra:
        raise ValueError(f"unknown report field(s): {', '.join(extra)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data['schema_version']!r}")
    for name in (
        "report_id",
        "task_id",
        "verifier_id",
        "verifier_version",
        "runtime_version",
        "signing_key_id",
    ):
        _require_string(data, name)
    _require_string(data, "task_title", allow_empty=True)
    _require_optional_string(data, "run_id")
    _require_optional_string(data, "error")
    if not isinstance(data["passed"], bool):
        raise ValueError("passed must be a boolean")
    for name in ("payloads_disclosed", "evidence_disclosed", "metadata_disclosed"):
        if not isinstance(data[name], bool):
            raise ValueError(f"{name} must be a boolean")
    score = data["score"]
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        raise ValueError("score must be a finite number or null")
    for name in (
        "evidence",
        "metadata",
        "input_payload",
        "output_payload",
        "task",
        "result",
    ):
        _require_dict(data, name)
    for name in (
        "input_hash",
        "output_hash",
        "evidence_hash",
        "metadata_hash",
        "report_hash",
        "signature",
    ):
        value = _require_string(data, name)
        if not _is_sha256(value):
            raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    previous_hash = _require_optional_string(data, "previous_hash")
    if previous_hash is not None and not _is_sha256(previous_hash):
        raise ValueError("previous_hash must be a lowercase SHA-256 hex digest or null")
    if data["signature_version"] != SIGNATURE_VERSION:
        raise ValueError(f"unsupported signature_version: {data['signature_version']!r}")
    created_at = _require_string(data, "created_at")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    if data["task"] != data["input_payload"]:
        raise ValueError("task and input_payload disclosures must match exactly")
    if data["result"] != data["output_payload"]:
        raise ValueError("result and output_payload disclosures must match exactly")
    if data["payloads_disclosed"]:
        if not data["input_payload"] or not data["output_payload"]:
            raise ValueError("disclosed payloads must include both input and output objects")
        if stable_hash(data["input_payload"]) != data["input_hash"]:
            raise ValueError("input hash mismatch")
        if stable_hash(data["output_payload"]) != data["output_hash"]:
            raise ValueError("output hash mismatch")
    elif data["input_payload"] or data["output_payload"] or data["task"] or data["result"]:
        raise ValueError("redacted payload fields must be empty objects")
    if data["evidence_disclosed"]:
        if stable_hash(data["evidence"]) != data["evidence_hash"]:
            raise ValueError("evidence hash mismatch")
    elif data["evidence"]:
        raise ValueError("redacted evidence must be an empty object")
    if data["metadata_disclosed"]:
        if stable_hash(data["metadata"]) != data["metadata_hash"]:
            raise ValueError("metadata hash mismatch")
    elif data["metadata"]:
        raise ValueError("redacted metadata must be an empty object")


def _validate_legacy_v1_fields(data: dict[str, Any]) -> None:
    """Validate the exact historical v1 wire shape without upgrading its trust."""
    missing = sorted(_LEGACY_V1_REPORT_FIELDS - set(data))
    if missing:
        raise ValueError(f"missing legacy report field(s): {', '.join(missing)}")
    extra = sorted(set(data) - _LEGACY_V1_REPORT_FIELDS)
    if extra:
        raise ValueError(f"unknown legacy report field(s): {', '.join(extra)}")
    if data["schema_version"] != LEGACY_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data['schema_version']!r}")
    for name in (
        "report_id",
        "task_id",
        "verifier_id",
        "verifier_version",
        "runtime_version",
    ):
        _require_string(data, name)
    _require_string(data, "task_title", allow_empty=True)
    _require_optional_string(data, "run_id")
    _require_optional_string(data, "error")
    if not isinstance(data["passed"], bool):
        raise ValueError("passed must be a boolean")
    score = data["score"]
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        raise ValueError("score must be a finite number or null")
    _require_dict(data, "evidence")
    _require_dict(data, "metadata")
    for name in ("input_hash", "output_hash", "report_hash"):
        value = _require_string(data, name)
        if not _is_sha256(value):
            raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    previous_hash = _require_optional_string(data, "previous_hash")
    if previous_hash is not None and not _is_sha256(previous_hash):
        raise ValueError("previous_hash must be a lowercase SHA-256 hex digest or null")
    created_at = _require_string(data, "created_at")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError("created_at must include a timezone")


def _legacy_v1_hash(data: dict[str, Any]) -> str:
    payload = dict(data)
    payload.pop("report_hash", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _key_bytes(signing_key: str | bytes | None) -> bytes:
    if signing_key is None:
        raise VerificationError("an explicit report signing key is required")
    encoded = signing_key.encode("utf-8") if isinstance(signing_key, str) else signing_key
    if not isinstance(encoded, bytes):
        raise VerificationError("report signing key must be text or bytes")
    if len(encoded) < MIN_SIGNING_KEY_BYTES:
        raise VerificationError(
            f"report signing key must contain at least {MIN_SIGNING_KEY_BYTES} bytes"
        )
    return encoded


def sign_report(
    report: VerificationReport,
    signing_key: str | bytes | None = None,
    *,
    signing_key_id: str = "operator",
) -> VerificationReport:
    """Sign a report using operator-supplied HMAC key material.

    There is deliberately no package default or implicit environment lookup.
    """
    key = _key_bytes(signing_key)
    if not signing_key_id:
        raise VerificationError("report signing_key_id must be non-empty")
    prepared = replace(
        report,
        signing_key_id=signing_key_id,
        signature_version=SIGNATURE_VERSION,
        signature="",
    )
    hashed = prepared.with_computed_hash()
    signature = hmac.new(key, hashed.report_hash.encode("utf-8"), sha256).hexdigest()
    return replace(hashed, signature=signature)


def _signature_valid(report: VerificationReport, signing_key: bytes) -> bool:
    if report.signature_version != SIGNATURE_VERSION or not report.signature:
        return False
    expected = hmac.new(
        signing_key,
        report.report_hash.encode("utf-8"),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, report.signature)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def _parse_line_payload(line: str) -> dict[str, Any]:
    payload = json.loads(line, object_pairs_hook=_no_duplicate_object)
    if not isinstance(payload, dict):
        raise ValueError("report line must contain a JSON object")
    return payload


def _limitations(
    anchored: bool,
    *,
    signed_count: int,
    legacy_unsigned_count: int,
) -> tuple[str, ...]:
    limitations: list[str] = []
    if signed_count:
        limitations.append(_KEY_HOLDER_LIMITATION)
    if legacy_unsigned_count:
        limitations.append(_LEGACY_UNSIGNED_LIMITATION)
    if not anchored:
        limitations.append(_UNANCHORED_LIMITATION)
    limitations.append(_COMMITMENT_LIMITATION)
    return tuple(limitations)


def _validate_text(
    text: str,
    *,
    require_signature: bool,
    signing_key: bytes | None,
    trusted_head: str | None,
    allow_legacy_v1: bool = False,
) -> ReportChainValidation:
    if not text:
        return ReportChainValidation(False, 0, "empty evidence chain")
    lines = text.splitlines()
    if not lines or not any(line.strip() for line in lines):
        return ReportChainValidation(False, 0, "empty evidence chain")

    previous_hash: str | None = None
    checked = 0
    signed_count = 0
    legacy_unsigned_count = 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: blank lines are not allowed",
                head_hash=previous_hash,
            )
        try:
            payload = _parse_line_payload(line)
        except (TypeError, ValueError, json.JSONDecodeError, VerificationError) as exc:
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: invalid JSON report: {exc}",
                head_hash=previous_hash,
            )

        schema_version = payload.get("schema_version")
        if schema_version == LEGACY_SCHEMA_VERSION:
            if not allow_legacy_v1:
                return ReportChainValidation(
                    False,
                    checked,
                    f"line {line_number}: legacy {LEGACY_SCHEMA_VERSION} requires "
                    "allow_legacy_v1=True; legacy records are unsigned",
                    head_hash=previous_hash,
                )
            if signed_count:
                return ReportChainValidation(
                    False,
                    checked,
                    f"line {line_number}: legacy records cannot follow signed v2 records",
                    head_hash=previous_hash,
                )
            try:
                _validate_legacy_v1_fields(payload)
            except (TypeError, ValueError) as exc:
                return ReportChainValidation(
                    False,
                    checked,
                    f"line {line_number}: invalid legacy JSON report: {exc}",
                    head_hash=previous_hash,
                )
            record_previous_hash = payload["previous_hash"]
            computed_hash = _legacy_v1_hash(payload)
            report_hash = payload["report_hash"]
            legacy_unsigned_count += 1
        else:
            try:
                report = VerificationReport.from_dict(payload, strict=True)
            except (TypeError, ValueError, VerificationError) as exc:
                return ReportChainValidation(
                    False,
                    checked,
                    f"line {line_number}: invalid JSON report: {exc}",
                    head_hash=previous_hash,
                )
            record_previous_hash = report.previous_hash
            computed_hash = report.compute_hash()
            report_hash = report.report_hash
            if require_signature:
                if signing_key is None:
                    return ReportChainValidation(
                        False,
                        checked,
                        "an explicit report signing key is required",
                        head_hash=previous_hash,
                    )
                if not _signature_valid(report, signing_key):
                    return ReportChainValidation(
                        False,
                        checked,
                        f"line {line_number}: signature mismatch",
                        head_hash=previous_hash,
                    )
            signed_count += 1

        if record_previous_hash != previous_hash:
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: previous_hash mismatch",
                head_hash=previous_hash,
            )
        if computed_hash != report_hash:
            return ReportChainValidation(
                False,
                checked,
                f"line {line_number}: hash mismatch",
                head_hash=previous_hash,
            )
        previous_hash = report_hash
        checked += 1

    anchored = trusted_head is not None
    if trusted_head is not None and previous_hash != trusted_head:
        return ReportChainValidation(
            False,
            checked,
            "trusted head mismatch",
            anchored=False,
            head_hash=previous_hash,
        )
    return ReportChainValidation(
        True,
        checked,
        anchored=anchored,
        head_hash=previous_hash,
        limitations=_limitations(
            anchored,
            signed_count=signed_count,
            legacy_unsigned_count=legacy_unsigned_count,
        ),
        legacy_unsigned_count=legacy_unsigned_count,
    )


def validate_report_chain(
    path: str | Path,
    *,
    require_signature: bool = True,
    signing_key: str | bytes | None = None,
    trusted_head: str | None = None,
    allow_legacy_v1: bool = False,
) -> ReportChainValidation:
    """Strictly validate all records, links, signatures, and optional anchor.

    A successful unanchored result proves only the internal integrity of the
    file under the supplied symmetric key. ``allow_legacy_v1=True`` additionally
    permits read-only validation of historical unsigned v1 records; it never
    upgrades them to signed evidence. The result's ``limitations`` and
    ``legacy_unsigned_count`` fields state the remaining guarantees explicitly.
    """
    report_path = Path(path)
    if not report_path.exists():
        return ReportChainValidation(False, 0, "evidence chain missing")
    if trusted_head is not None and not _is_sha256(trusted_head):
        return ReportChainValidation(False, 0, "trusted_head must be a SHA-256 digest")
    key: bytes | None = None
    if require_signature and (signing_key is not None or not allow_legacy_v1):
        try:
            key = _key_bytes(signing_key)
        except VerificationError as exc:
            return ReportChainValidation(False, 0, str(exc))
    try:
        text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return ReportChainValidation(False, 0, f"evidence chain unreadable: {exc}")
    return _validate_text(
        text,
        require_signature=require_signature,
        signing_key=key,
        trusted_head=trusted_head,
        allow_legacy_v1=allow_legacy_v1,
    )


def latest_report_hash(path: str | Path) -> str | None:
    """Return a structurally validated chain head (not an external trust anchor)."""
    report_path = Path(path)
    if not report_path.exists():
        return None
    validation = validate_report_chain(report_path, require_signature=False)
    if not validation.valid:
        raise VerificationError(f"invalid report chain: {validation.error}")
    return validation.head_hash


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, UnicodeError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise VerificationError(f"durable report write failed: {exc}") from exc


def append_report_jsonl(
    path: str | Path,
    report: VerificationReport,
    *,
    signing_key: str | bytes | None = None,
    signing_key_id: str = "operator",
    lock_timeout: float = 15.0,
) -> VerificationReport:
    """Validate then atomically replace a signed JSONL evidence chain.

    This is intentionally a whole-file update: it prevents a torn append from
    being mistaken for an acknowledged record. It is suitable for the legacy
    local report path, not an unbounded high-throughput transparency log.
    """
    key = _key_bytes(signing_key)
    if lock_timeout <= 0:
        raise VerificationError("report lock_timeout must be greater than zero")
    report_path = Path(path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VerificationError(f"cannot create report directory: {exc}") from exc
    lock = FileLock(str(report_path) + ".lock", timeout=lock_timeout)
    try:
        with lock:
            existing = ""
            previous_hash: str | None = None
            if report_path.exists():
                try:
                    existing = report_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise VerificationError(f"evidence chain unreadable: {exc}") from exc
                validation = _validate_text(
                    existing,
                    require_signature=True,
                    signing_key=key,
                    trusted_head=None,
                )
                if not validation.valid:
                    raise VerificationError(f"existing evidence chain invalid: {validation.error}")
                previous_hash = validation.head_hash

            chained = sign_report(
                report.with_previous_hash(previous_hash),
                key,
                signing_key_id=signing_key_id,
            )
            prefix = existing
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            candidate = prefix + _canonical_json(chained.to_dict()) + "\n"
            candidate_validation = _validate_text(
                candidate,
                require_signature=True,
                signing_key=key,
                trusted_head=None,
            )
            if not candidate_validation.valid:
                raise VerificationError(
                    f"refusing to persist invalid report: {candidate_validation.error}"
                )
            _atomic_write_text(report_path, candidate)
            return chained
    except Timeout as exc:
        raise VerificationError(f"timed out acquiring report lock: {report_path}") from exc
