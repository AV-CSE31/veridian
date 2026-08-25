"""Framework-agnostic verification contracts and proof bundles."""

from __future__ import annotations

import hmac
import json
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from veridian import __version__
from veridian.core.exceptions import VeridianConfigError, VerificationError
from veridian.core.report import stable_hash
from veridian.core.task import Task, TaskResult
from veridian.verify.base import VerificationResult, VerifierRegistry, registry

PROOF_SCHEMA_VERSION = "veridian-proof.v2"
PROOF_SIGNATURE_VERSION = "hmac-sha256.v1"
MIN_PROOF_SIGNING_KEY_BYTES = 32
SigningKey = str | bytes | None

_SYMMETRIC_KEY_LIMITATION = (
    "HMAC is symmetric: any completion-proof signing-key holder can rewrite a valid chain."
)
_UNANCHORED_CHAIN_LIMITATION = (
    "The proof head was not compared with an independently retained anchor; "
    "rollback, truncation, fork selection, freshness, and existence are not proven."
)


@dataclass(frozen=True)
class VerifierStep:
    """One verifier invocation inside a completion contract."""

    verifier_id: str
    verifier_config: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    blocking: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.verifier_id, str) or not self.verifier_id.strip():
            raise VeridianConfigError("VerifierStep.verifier_id must not be empty")
        if not isinstance(self.verifier_config, dict):
            raise VeridianConfigError("VerifierStep.verifier_config must be an object")
        if self.name is not None and not isinstance(self.name, str):
            raise VeridianConfigError("VerifierStep.name must be a string or None")
        if not isinstance(self.blocking, bool):
            raise VeridianConfigError("VerifierStep.blocking must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "verifier_config": self.verifier_config,
            "name": self.name,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class VerificationContract:
    """A portable contract that decides whether agent work is complete."""

    contract_id: str
    verifiers: list[VerifierStep]
    description: str = ""
    evidence_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise VeridianConfigError("VerificationContract.contract_id must not be empty")
        if not self.verifiers:
            raise VeridianConfigError(
                "VerificationContract.verifiers must contain at least one step"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "description": self.description,
            "verifiers": [step.to_dict() for step in self.verifiers],
            "evidence_files": self.evidence_files,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class VerificationDecision:
    """Stable completion decision and audit proof bundle."""

    decision_id: str
    contract_id: str
    passed: bool
    blocking: bool
    feedback: list[str]
    verifier_results: list[dict[str, Any]]
    evidence: dict[str, Any]
    input_hash: str
    output_hash: str
    created_at: str
    runtime_version: str
    contract_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    previous_hash: str | None = None
    signature_version: str = PROOF_SIGNATURE_VERSION
    signature: str = ""
    proof_hash: str = ""
    schema_version: str = PROOF_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "contract_id": self.contract_id,
            "contract_hash": self.contract_hash,
            "passed": self.passed,
            "blocking": self.blocking,
            "feedback": self.feedback,
            "verifier_results": self.verifier_results,
            "evidence": self.evidence,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "created_at": self.created_at,
            "runtime_version": self.runtime_version,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "signature_version": self.signature_version,
            "signature": self.signature,
            "proof_hash": self.proof_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> VerificationDecision:
        """Restore a decision, optionally rejecting missing or unknown fields."""
        if strict:
            unknown = sorted(set(data) - _DECISION_FIELDS)
            if unknown:
                raise VerificationError(f"unknown completion proof field(s): {', '.join(unknown)}")
            missing = sorted(_DECISION_FIELDS - set(data))
            if missing:
                raise VerificationError(f"missing completion proof field(s): {', '.join(missing)}")

        def require_string(name: str, *, allow_empty: bool = False) -> str:
            value = data.get(name)
            if not isinstance(value, str) or (not allow_empty and not value):
                raise VerificationError(f"completion proof {name} must be a string")
            return value

        def require_dict(name: str) -> dict[str, Any]:
            value = data.get(name)
            if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
                raise VerificationError(f"completion proof {name} must be an object")
            return dict(value)

        feedback_value = data.get("feedback")
        if not isinstance(feedback_value, list) or not all(
            isinstance(item, str) for item in feedback_value
        ):
            raise VerificationError("completion proof feedback must be a list of strings")
        results_value = data.get("verifier_results")
        if not isinstance(results_value, list) or not all(
            isinstance(item, dict) for item in results_value
        ):
            raise VerificationError("completion proof verifier_results must be a list of objects")
        passed = data.get("passed")
        blocking = data.get("blocking")
        if not isinstance(passed, bool) or not isinstance(blocking, bool):
            raise VerificationError("completion proof decision flags must be booleans")
        previous_hash = data.get("previous_hash")
        if previous_hash is not None and not isinstance(previous_hash, str):
            raise VerificationError("completion proof previous_hash must be a string or null")

        return cls(
            schema_version=require_string("schema_version"),
            decision_id=require_string("decision_id"),
            contract_id=require_string("contract_id"),
            contract_hash=require_string("contract_hash"),
            passed=passed,
            blocking=blocking,
            feedback=list(feedback_value),
            verifier_results=[dict(item) for item in results_value],
            evidence=require_dict("evidence"),
            input_hash=require_string("input_hash"),
            output_hash=require_string("output_hash"),
            created_at=require_string("created_at"),
            runtime_version=require_string("runtime_version"),
            metadata=require_dict("metadata"),
            previous_hash=previous_hash,
            signature_version=require_string("signature_version"),
            signature=require_string("signature"),
            proof_hash=require_string("proof_hash"),
        )

    def hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("proof_hash", None)
        payload.pop("signature", None)
        return payload

    def compute_hash(self) -> str:
        return stable_hash(self.hash_payload())

    def with_proof_hash(self) -> VerificationDecision:
        return replace(self, proof_hash=self.compute_hash())

    def to_pr_comment(self) -> str:
        icon = "PASS" if self.passed else "BLOCKED"
        lines = [
            f"### Veridian completion gate: {icon}",
            "",
            f"- Contract: `{self.contract_id}`",
            f"- Proof hash: `{self.proof_hash}`",
            f"- Blocking: `{self.blocking}`",
        ]
        if self.feedback:
            lines.extend(["", "Feedback:"])
            lines.extend(f"- {item}" for item in self.feedback)
        return "\n".join(lines) + "\n"


_DECISION_FIELDS = frozenset(
    VerificationDecision(
        decision_id="",
        contract_id="",
        passed=False,
        blocking=False,
        feedback=[],
        verifier_results=[],
        evidence={},
        input_hash="",
        output_hash="",
        created_at="",
        runtime_version="",
    ).to_dict()
)


@dataclass(frozen=True)
class ProofChainValidation:
    """Outcome of validating a complete completion-proof JSONL chain."""

    valid: bool
    checked_count: int
    head_hash: str | None = None
    error: str | None = None
    anchored: bool = False
    limitations: tuple[str, ...] = (
        _SYMMETRIC_KEY_LIMITATION,
        _UNANCHORED_CHAIN_LIMITATION,
    )


def verify_completion(
    *,
    contract: VerificationContract,
    input_payload: Any,
    output_payload: Any,
    proof_file: str | Path | None = None,
    verifier_registry: VerifierRegistry | None = None,
    metadata: dict[str, Any] | None = None,
    signing_key: SigningKey = None,
    include_payloads: bool = False,
    include_diagnostics: bool = False,
) -> VerificationDecision:
    """Run a completion contract against arbitrary agent input/output.

    This is the framework-adapter surface. LangGraph nodes, Pydantic AI agents,
    OpenAI Agent guardrails, CI jobs, and custom loops can call it without
    adopting ``VeridianRunner``.

    Raw input/output payloads and verifier diagnostics are commitment-only by
    default. Set ``include_payloads`` or ``include_diagnostics`` only when the
    destination proof store is approved for that potentially sensitive data.
    """
    key = _resolve_signing_key(signing_key)
    active_registry = verifier_registry or registry
    result = _coerce_result(output_payload)
    evidence: dict[str, Any] = {
        "evidence_files": _hash_evidence_files(contract.evidence_files),
    }
    if include_payloads:
        evidence.update(
            {
                "input_payload": input_payload,
                "output_payload": _result_payload_for_proof(result),
            }
        )
    feedback: list[str] = []
    verifier_results: list[dict[str, Any]] = []
    overall_passed = True
    blocking = False

    missing_evidence = [
        item["path"] for item in evidence["evidence_files"] if item.get("missing") is True
    ]
    if missing_evidence:
        overall_passed = False
        blocking = True
        feedback.append(f"Missing evidence file(s): {', '.join(missing_evidence)}")

    for index, step in enumerate(contract.verifiers, start=1):
        task = Task(
            title=contract.contract_id,
            description=contract.description
            or f"Verify completion contract {contract.contract_id}",
            verifier_id=step.verifier_id,
            verifier_config=dict(step.verifier_config),
            metadata={
                "contract_id": contract.contract_id,
                "input_payload_hash": stable_hash(input_payload),
                "step_index": index,
            },
        )
        verifier_raised = False
        try:
            verifier = active_registry.get(step.verifier_id, step.verifier_config or None)
            verification = verifier.verify(task, result)
            if not isinstance(verification, VerificationResult):
                raise VerificationError("verifier returned an invalid result object")
            if not isinstance(verification.passed, bool):
                raise VerificationError("verifier result passed must be a boolean")
            if verification.error is not None and not isinstance(verification.error, str):
                raise VerificationError("verifier result error must be a string or None")
            if not isinstance(verification.evidence, dict):
                raise VerificationError("verifier result evidence must be an object")
            step_passed = verification.passed
            step_error = verification.error or ""
            step_evidence = verification.evidence
        except Exception as exc:
            verifier_raised = True
            step_passed = False
            step_error = str(exc)[:300]
            step_evidence = {"exception": type(exc).__name__}

        if include_diagnostics:
            recorded_error = step_error or None
            recorded_evidence = step_evidence
        else:
            recorded_error = None
            if not step_passed:
                recorded_error = (
                    "verifier execution failed" if verifier_raised else "verifier reported failure"
                )
            recorded_evidence = {}
        effective_blocking = step.blocking or verifier_raised

        verifier_results.append(
            {
                "step": index,
                "name": step.name or step.verifier_id,
                "verifier_id": step.verifier_id,
                "passed": step_passed,
                "blocking": effective_blocking,
                "error": recorded_error,
                "error_hash": stable_hash(step_error) if step_error else None,
                "evidence": recorded_evidence,
                "evidence_hash": stable_hash(step_evidence),
            }
        )
        if not step_passed:
            feedback.append(
                step_error
                if include_diagnostics and step_error
                else f"Verifier {step.verifier_id!r} failed"
            )
            if effective_blocking:
                overall_passed = False
                blocking = True

    decision = VerificationDecision(
        decision_id=str(uuid.uuid4()),
        contract_id=contract.contract_id,
        passed=overall_passed,
        blocking=blocking,
        feedback=feedback,
        verifier_results=verifier_results,
        evidence=evidence,
        input_hash=stable_hash(input_payload),
        output_hash=stable_hash(_result_payload_for_proof(result)),
        created_at=datetime.now(tz=UTC).isoformat(),
        runtime_version=__version__,
        contract_hash=stable_hash(contract.to_dict()),
        metadata={**contract.metadata, **(metadata or {})},
    )
    if proof_file is not None:
        return append_decision_jsonl(proof_file, decision, signing_key=key)
    return sign_decision(decision, signing_key=key)


def append_decision_jsonl(
    path: str | Path,
    decision: VerificationDecision,
    *,
    signing_key: SigningKey = None,
    lock_timeout: float = 15.0,
) -> VerificationDecision:
    """Atomically append a signed decision to a validated JSONL proof chain.

    Existing content is validated before a new link is prepared. Malformed,
    tampered, or replayed histories are never treated as a new empty chain.
    """
    key = _resolve_signing_key(signing_key)
    if lock_timeout <= 0:
        raise VerificationError("completion proof lock_timeout must be greater than zero")
    proof_path = Path(path)
    try:
        proof_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VerificationError(f"unable to create completion proof directory: {exc}") from exc
    lock = FileLock(str(proof_path) + ".lock", timeout=lock_timeout)
    try:
        with lock:
            previous_hash: str | None = None
            existing = b""
            if proof_path.exists():
                validation = validate_proof_chain(proof_path, signing_key=key)
                if not validation.valid:
                    raise VerificationError(
                        "existing completion proof chain is invalid: "
                        f"{validation.error or 'unknown validation failure'}"
                    )
                previous_hash = validation.head_hash
                existing = proof_path.read_bytes()
            chained = sign_decision(
                replace(decision, previous_hash=previous_hash),
                signing_key=key,
            )
            separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
            record = (
                json.dumps(
                    chained.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            candidate = existing + separator + record
            temporary = proof_path.with_name(f".{proof_path.name}.{uuid.uuid4().hex}.tmp")
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = None
                    handle.write(candidate)
                    handle.flush()
                    os.fsync(handle.fileno())
                candidate_validation = validate_proof_chain(temporary, signing_key=key)
                if not candidate_validation.valid:
                    raise VerificationError(
                        "new completion proof record would invalidate the chain: "
                        f"{candidate_validation.error or 'unknown validation failure'}"
                    )
                os.replace(temporary, proof_path)
                _fsync_parent_directory(proof_path.parent)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            return chained
    except Timeout as exc:
        raise VerificationError(f"timed out acquiring proof lock: {proof_path}") from exc
    except (OSError, TypeError, ValueError, UnicodeError) as exc:
        raise VerificationError(f"durable completion proof write failed: {exc}") from exc


def _fsync_parent_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def latest_proof_hash(path: str | Path, *, signing_key: SigningKey = None) -> str | None:
    """Return the authenticated chain head, failing closed on invalid content."""
    proof_path = Path(path)
    if not proof_path.exists():
        return None
    validation = validate_proof_chain(proof_path, signing_key=signing_key)
    if not validation.valid:
        raise VerificationError(
            f"completion proof chain is invalid: {validation.error or 'unknown validation failure'}"
        )
    return validation.head_hash


def validate_proof_chain(
    path: str | Path,
    *,
    signing_key: SigningKey = None,
    expected_head: str | None = None,
    expected_count: int | None = None,
) -> ProofChainValidation:
    """Validate every record, signature, and link in a completion-proof chain.

    ``expected_head`` and ``expected_count`` let a caller compare the local file
    with values retained in an independent trusted system. Without such an
    external anchor, no local file format can prove that the whole file was not
    replaced with a separately valid history.
    """
    key = _resolve_signing_key(signing_key)
    if expected_head is not None and not _is_sha256_hex(expected_head):
        return ProofChainValidation(False, 0, error="expected_head must be a SHA-256 digest")
    if expected_count is not None and (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 0
    ):
        return ProofChainValidation(False, 0, error="expected_count must be a non-negative integer")
    proof_path = Path(path)
    if not proof_path.exists():
        return ProofChainValidation(False, 0, error="completion proof chain missing")
    try:
        lines = proof_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return ProofChainValidation(False, 0, error=f"unable to read proof chain: {exc}")
    if not lines:
        return ProofChainValidation(False, 0, error="empty completion proof chain")

    previous_hash: str | None = None
    decision_ids: set[str] = set()
    proof_hashes: set[str] = set()
    checked = 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            return ProofChainValidation(
                False, checked, previous_hash, f"line {line_number}: blank proof record"
            )
        try:
            raw = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_json_fields,
                parse_constant=_reject_nonstandard_json_constant,
            )
            if not isinstance(raw, dict):
                raise VerificationError("completion proof record must be an object")
            decision = VerificationDecision.from_dict(raw, strict=True)
        except (TypeError, ValueError, json.JSONDecodeError, VerificationError) as exc:
            return ProofChainValidation(
                False, checked, previous_hash, f"line {line_number}: invalid proof: {exc}"
            )

        error = _validate_decision(decision, key=key, previous_hash=previous_hash)
        if error is not None:
            return ProofChainValidation(
                False, checked, previous_hash, f"line {line_number}: {error}"
            )
        if decision.decision_id in decision_ids:
            return ProofChainValidation(
                False, checked, previous_hash, f"line {line_number}: duplicate decision_id"
            )
        if decision.proof_hash in proof_hashes:
            return ProofChainValidation(
                False, checked, previous_hash, f"line {line_number}: replayed proof_hash"
            )
        decision_ids.add(decision.decision_id)
        proof_hashes.add(decision.proof_hash)
        previous_hash = decision.proof_hash
        checked += 1

    if expected_count is not None and checked != expected_count:
        return ProofChainValidation(
            False,
            checked,
            previous_hash,
            f"proof count mismatch: expected {expected_count}, got {checked}",
        )
    if expected_head is not None and previous_hash != expected_head:
        return ProofChainValidation(False, checked, previous_hash, "proof head mismatch")
    anchored = expected_head is not None
    limitations = (
        (_SYMMETRIC_KEY_LIMITATION,)
        if anchored
        else (_SYMMETRIC_KEY_LIMITATION, _UNANCHORED_CHAIN_LIMITATION)
    )
    return ProofChainValidation(
        True,
        checked,
        previous_hash,
        anchored=anchored,
        limitations=limitations,
    )


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _reject_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def _validate_decision(
    decision: VerificationDecision, *, key: bytes, previous_hash: str | None
) -> str | None:
    if decision.schema_version != PROOF_SCHEMA_VERSION:
        return f"unsupported schema_version {decision.schema_version!r}"
    if decision.signature_version != PROOF_SIGNATURE_VERSION:
        return f"unsupported signature_version {decision.signature_version!r}"
    if decision.previous_hash != previous_hash:
        return "previous_hash mismatch"
    for name, value in (
        ("contract_hash", decision.contract_hash),
        ("input_hash", decision.input_hash),
        ("output_hash", decision.output_hash),
        ("proof_hash", decision.proof_hash),
        ("signature", decision.signature),
    ):
        if not _is_sha256_hex(value):
            return f"{name} must be a lowercase SHA-256 digest"
    if decision.previous_hash is not None and not _is_sha256_hex(decision.previous_hash):
        return "previous_hash must be a lowercase SHA-256 digest"
    try:
        parsed_id = uuid.UUID(decision.decision_id)
    except ValueError:
        return "decision_id must be a UUID"
    if str(parsed_id) != decision.decision_id:
        return "decision_id must use canonical UUID form"
    try:
        created_at = datetime.fromisoformat(decision.created_at)
    except ValueError:
        return "created_at must be an ISO-8601 timestamp"
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        return "created_at must include a UTC offset"
    if decision.compute_hash() != decision.proof_hash:
        return "proof hash mismatch"
    expected_signature = hmac.new(key, decision.proof_hash.encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, decision.signature):
        return "signature mismatch"

    has_input = "input_payload" in decision.evidence
    has_output = "output_payload" in decision.evidence
    if has_input != has_output:
        return "embedded input and output payloads must appear together"
    if has_input:
        if stable_hash(decision.evidence["input_payload"]) != decision.input_hash:
            return "embedded input payload hash mismatch"
        if stable_hash(decision.evidence["output_payload"]) != decision.output_hash:
            return "embedded output payload hash mismatch"
    return None


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def sign_decision(
    decision: VerificationDecision, signing_key: SigningKey = None
) -> VerificationDecision:
    """Return a decision signed by an operator-supplied HMAC key.

    The key must be passed explicitly or provided through
    ``VERIDIAN_PROOF_SIGNING_KEY``. Veridian intentionally has no built-in
    fallback key: a public shared secret would let any proof holder forge an
    arbitrary completion history.
    """
    try:
        hashed = replace(
            decision,
            signature="",
            proof_hash="",
            signature_version=PROOF_SIGNATURE_VERSION,
        ).with_proof_hash()
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"completion proof is not canonical JSON: {exc}") from exc
    key = _resolve_signing_key(signing_key)
    signature = hmac.new(key, hashed.proof_hash.encode("utf-8"), sha256).hexdigest()
    signed = replace(hashed, signature=signature)
    try:
        error = _validate_decision(signed, key=key, previous_hash=signed.previous_hash)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"completion proof is not canonical JSON: {exc}") from exc
    if error is not None:
        raise VerificationError(f"refusing to sign invalid completion proof: {error}")
    return signed


def _resolve_signing_key(signing_key: SigningKey) -> bytes:
    key = signing_key if signing_key is not None else os.getenv("VERIDIAN_PROOF_SIGNING_KEY")
    if key is None:
        raise VeridianConfigError(
            "completion proof signing requires signing_key or VERIDIAN_PROOF_SIGNING_KEY"
        )
    encoded = key.encode("utf-8") if isinstance(key, str) else key
    if not isinstance(encoded, bytes):
        raise VeridianConfigError("completion proof signing key must be text or bytes")
    if len(encoded) < MIN_PROOF_SIGNING_KEY_BYTES:
        raise VeridianConfigError(
            f"completion proof signing key must be at least {MIN_PROOF_SIGNING_KEY_BYTES} bytes"
        )
    return encoded


def _coerce_result(output_payload: Any) -> TaskResult:
    if isinstance(output_payload, TaskResult):
        return output_payload
    if isinstance(output_payload, dict):
        return TaskResult(
            raw_output=json.dumps(output_payload, sort_keys=True), structured=output_payload
        )
    return TaskResult(raw_output=str(output_payload))


def _result_payload_for_proof(result: TaskResult) -> dict[str, Any]:
    return {
        "raw_output": result.raw_output,
        "structured": result.structured,
        "artifacts": result.artifacts,
        "bash_outputs": result.bash_outputs,
        "token_usage": result.token_usage,
        "tool_calls": result.tool_calls,
    }


def _hash_evidence_files(files: list[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for file_name in files:
        path = Path(file_name)
        if not path.exists() or not path.is_file():
            evidence.append({"path": file_name, "missing": True})
            continue
        data = path.read_bytes()
        evidence.append(
            {
                "path": file_name,
                "size_bytes": len(data),
                "sha256": sha256(data).hexdigest(),
            }
        )
    return evidence
