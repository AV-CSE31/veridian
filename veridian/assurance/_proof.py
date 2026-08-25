"""Portable proof bundles and offline byte-integrity verification."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum

from veridian.core.exceptions import VeridianError

from ._attestation import (
    RECEIPT_PAYLOAD_TYPE,
    ReceiptStatementV1,
    VerificationKeyProvider,
    _InvalidSignature,
    _UntrustedSigner,
    _verify_envelope,
)
from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    require_exact_fields,
    require_string,
    sha256_digest,
)
from ._context import (
    AnchorHead,
    HistoryStatus,
    ProofVerificationContext,
    ReplayStatus,
    evaluate_replay,
    validate_witnesses,
)
from ._decision import DecisionPayloadV1
from ._errors import AssuranceError, AssuranceValidationError
from ._evidence import EvidenceRef
from ._model import (
    ActionSemanticsV1,
    AuthorizationEnvelope,
    CompletionSemanticsV1,
    TransportBinding,
    parse_utc_second,
)
from ._snapshot import VerificationSnapshotV1
from ._verifier_execution import VerifierManifestV1

PROOF_BUNDLE_SCHEMA_ID = "veridian.proof-bundle.v1"


class ProofErrorCode(StrEnum):
    UNTRUSTED_SIGNER = "untrusted-signer"
    SIGNATURE_INVALID = "signature-invalid"
    RECEIPT_INVALID = "receipt-invalid"
    DECISION_DIGEST_MISMATCH = "decision-digest-mismatch"
    DECISION_INVALID = "decision-invalid"
    AUTHORIZATION_BINDING_MISMATCH = "authorization-binding-mismatch"
    SUBJECT_BINDING_MISMATCH = "subject-binding-mismatch"
    CONTRACT_BINDING_MISMATCH = "contract-binding-mismatch"
    SNAPSHOT_BINDING_MISMATCH = "snapshot-binding-mismatch"
    TRANSPORT_BINDING_MISMATCH = "transport-binding-mismatch"
    VERIFIER_MANIFEST_MISMATCH = "verifier-manifest-mismatch"
    EVIDENCE_BINDING_MISMATCH = "evidence-binding-mismatch"
    EVIDENCE_STALE = "evidence-stale"
    POLICY_BINDING_MISMATCH = "policy-binding-mismatch"
    REPLAY_INVALID = "replay-invalid"
    ANCHOR_INVALID = "anchor-invalid"
    WITNESS_INVALID = "witness-invalid"


@dataclass(frozen=True)
class ProofBundleV1:
    """Exact signed and digest-bound artifacts needed by an offline verifier."""

    semantic_bytes: bytes
    authorization_envelope_bytes: bytes
    contract_bytes: bytes
    snapshot_bytes: bytes
    transport_binding_bytes: bytes
    verifier_manifest_bytes: tuple[bytes, ...]
    evidence_ref_bytes: tuple[bytes, ...]
    decision_bytes: bytes
    receipt_envelope_bytes: bytes
    witness_envelope_bytes: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "semantic_bytes",
            "authorization_envelope_bytes",
            "contract_bytes",
            "snapshot_bytes",
            "transport_binding_bytes",
            "decision_bytes",
            "receipt_envelope_bytes",
        ):
            if not isinstance(getattr(self, field_name), bytes):
                raise AssuranceValidationError(f"{field_name} must be exact bytes")
        for field_name in (
            "verifier_manifest_bytes",
            "evidence_ref_bytes",
            "witness_envelope_bytes",
        ):
            values = tuple(getattr(self, field_name))
            if not all(isinstance(value, bytes) for value in values):
                raise AssuranceValidationError(f"{field_name} must contain exact bytes")
            object.__setattr__(self, field_name, values)

    def to_bytes(self) -> bytes:
        def encode(value: bytes) -> str:
            return base64.b64encode(value).decode("ascii")

        return encode_profile_v1(
            {
                "schema_id": PROOF_BUNDLE_SCHEMA_ID,
                "semantic_bytes_b64": encode(self.semantic_bytes),
                "authorization_envelope_bytes_b64": encode(self.authorization_envelope_bytes),
                "contract_bytes_b64": encode(self.contract_bytes),
                "snapshot_bytes_b64": encode(self.snapshot_bytes),
                "transport_binding_bytes_b64": encode(self.transport_binding_bytes),
                "verifier_manifest_bytes_b64": [
                    encode(value) for value in self.verifier_manifest_bytes
                ],
                "evidence_ref_bytes_b64": [encode(value) for value in self.evidence_ref_bytes],
                "decision_bytes_b64": encode(self.decision_bytes),
                "receipt_envelope_bytes_b64": encode(self.receipt_envelope_bytes),
                "witness_envelope_bytes_b64": [
                    encode(value) for value in self.witness_envelope_bytes
                ],
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ProofBundleV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "semantic_bytes_b64",
                "authorization_envelope_bytes_b64",
                "contract_bytes_b64",
                "snapshot_bytes_b64",
                "transport_binding_bytes_b64",
                "verifier_manifest_bytes_b64",
                "evidence_ref_bytes_b64",
                "decision_bytes_b64",
                "receipt_envelope_bytes_b64",
                "witness_envelope_bytes_b64",
            }
        )
        require_exact_fields(payload, fields, "ProofBundleV1")
        if payload["schema_id"] != PROOF_BUNDLE_SCHEMA_ID:
            raise AssuranceValidationError("unsupported proof bundle schema")

        def decode(field_name: str) -> bytes:
            try:
                return base64.b64decode(
                    require_string(payload[field_name], field_name), validate=True
                )
            except (ValueError, binascii.Error) as exc:
                raise AssuranceValidationError(f"{field_name} contains invalid base64") from exc

        def decode_array(field_name: str) -> tuple[bytes, ...]:
            raw = payload[field_name]
            if not isinstance(raw, (list, tuple)):
                raise AssuranceValidationError(f"{field_name} must be an array")
            result: list[bytes] = []
            for value in raw:
                try:
                    result.append(
                        base64.b64decode(require_string(value, field_name), validate=True)
                    )
                except (ValueError, binascii.Error) as exc:
                    raise AssuranceValidationError(f"{field_name} contains invalid base64") from exc
            return tuple(result)

        return cls(
            semantic_bytes=decode("semantic_bytes_b64"),
            authorization_envelope_bytes=decode("authorization_envelope_bytes_b64"),
            contract_bytes=decode("contract_bytes_b64"),
            snapshot_bytes=decode("snapshot_bytes_b64"),
            transport_binding_bytes=decode("transport_binding_bytes_b64"),
            verifier_manifest_bytes=decode_array("verifier_manifest_bytes_b64"),
            evidence_ref_bytes=decode_array("evidence_ref_bytes_b64"),
            decision_bytes=decode("decision_bytes_b64"),
            receipt_envelope_bytes=decode("receipt_envelope_bytes_b64"),
            witness_envelope_bytes=decode_array("witness_envelope_bytes_b64"),
        )


@dataclass(frozen=True)
class ProofVerification:
    """Structured verification outcome; invalid artifacts never become decisions."""

    valid: bool
    error_code: ProofErrorCode | None
    error: str | None
    decision_digest: str | None = None
    receipt_id: str | None = None
    verified_signer_ids: tuple[str, ...] = ()
    replay_status: ReplayStatus = ReplayStatus.NOT_CHECKED
    history_status: HistoryStatus = HistoryStatus.UNANCHORED


def _invalid(
    code: ProofErrorCode,
    error: str,
    *,
    replay_status: ReplayStatus = ReplayStatus.NOT_CHECKED,
    history_status: HistoryStatus = HistoryStatus.UNANCHORED,
) -> ProofVerification:
    return ProofVerification(
        valid=False,
        error_code=code,
        error=error,
        replay_status=replay_status,
        history_status=history_status,
    )


def verify_proof_bundle(
    bundle: ProofBundleV1,
    receipt_keys: VerificationKeyProvider,
    *,
    context: ProofVerificationContext | None = None,
) -> ProofVerification:
    """Verify exact bytes and every disclosed digest binding offline.

    Without external context the result explicitly remains replay-unchecked and
    unanchored; a valid signature alone never claims freshness or append-only history.
    """
    try:
        envelope = _verify_envelope(bundle.receipt_envelope_bytes, receipt_keys)
    except _UntrustedSigner as exc:
        return _invalid(ProofErrorCode.UNTRUSTED_SIGNER, str(exc))
    except _InvalidSignature as exc:
        return _invalid(ProofErrorCode.SIGNATURE_INVALID, str(exc))
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.SIGNATURE_INVALID, str(exc))
    if envelope.payload_type != RECEIPT_PAYLOAD_TYPE:
        return _invalid(ProofErrorCode.RECEIPT_INVALID, "wrong receipt payload type")
    try:
        receipt = ReceiptStatementV1.from_bytes(envelope.payload)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.RECEIPT_INVALID, str(exc))
    decision_digest = sha256_digest(bundle.decision_bytes)
    if receipt.decision_digest != decision_digest:
        return _invalid(
            ProofErrorCode.DECISION_DIGEST_MISMATCH,
            "receipt does not bind the exact decision bytes",
        )
    try:
        decision = DecisionPayloadV1.from_bytes(bundle.decision_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.DECISION_INVALID, str(exc))
    if sha256_digest(bundle.authorization_envelope_bytes) != decision.authorization_envelope_digest:
        return _invalid(
            ProofErrorCode.AUTHORIZATION_BINDING_MISMATCH,
            "decision does not bind the authorization envelope bytes",
        )
    try:
        authorization = AuthorizationEnvelope.from_bytes(bundle.authorization_envelope_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.AUTHORIZATION_BINDING_MISMATCH, str(exc))
    if sha256_digest(bundle.semantic_bytes) != authorization.semantic_digest:
        return _invalid(
            ProofErrorCode.SUBJECT_BINDING_MISMATCH,
            "authorization does not bind the semantic subject bytes",
        )
    try:
        if authorization.semantic_kind == "action":
            ActionSemanticsV1.from_bytes(bundle.semantic_bytes)
        else:
            CompletionSemanticsV1.from_bytes(bundle.semantic_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.SUBJECT_BINDING_MISMATCH, str(exc))
    if sha256_digest(bundle.contract_bytes) != decision.contract_digest:
        return _invalid(
            ProofErrorCode.CONTRACT_BINDING_MISMATCH,
            "decision does not bind the exact contract bytes",
        )
    if sha256_digest(bundle.snapshot_bytes) != decision.snapshot_digest:
        return _invalid(
            ProofErrorCode.SNAPSHOT_BINDING_MISMATCH,
            "decision does not bind the exact snapshot bytes",
        )
    try:
        snapshot = VerificationSnapshotV1.from_bytes(bundle.snapshot_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.SNAPSHOT_BINDING_MISMATCH, str(exc))
    if (
        snapshot.authorization_envelope_digest != authorization.digest
        or snapshot.state_digest != authorization.state_digest
    ):
        return _invalid(
            ProofErrorCode.SNAPSHOT_BINDING_MISMATCH,
            "snapshot does not bind the authorization and state",
        )
    if sha256_digest(bundle.transport_binding_bytes) != receipt.transport_binding_digest:
        return _invalid(
            ProofErrorCode.TRANSPORT_BINDING_MISMATCH,
            "receipt does not bind the exact transport bytes",
        )
    try:
        TransportBinding.from_bytes(bundle.transport_binding_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.TRANSPORT_BINDING_MISMATCH, str(exc))
    try:
        manifests = tuple(
            VerifierManifestV1.from_bytes(item) for item in bundle.verifier_manifest_bytes
        )
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.VERIFIER_MANIFEST_MISMATCH, str(exc))
    manifest_digests = tuple(manifest.digest for manifest in manifests)
    if (
        manifest_digests != decision.verifier_manifest_digests
        or manifest_digests != snapshot.verifier_manifest_digests
    ):
        return _invalid(
            ProofErrorCode.VERIFIER_MANIFEST_MISMATCH,
            "proof does not disclose the exact verifier manifests",
        )
    try:
        evidence_refs = tuple(EvidenceRef.from_bytes(item) for item in bundle.evidence_ref_bytes)
    except AssuranceError as exc:
        return _invalid(ProofErrorCode.EVIDENCE_BINDING_MISMATCH, str(exc))
    evidence_digests = tuple(item.digest for item in evidence_refs)
    evidence_ids = {item.evidence_id for item in evidence_refs}
    required_evidence_ids = {
        evidence_id for clause in decision.clause_results for evidence_id in clause.evidence_ids
    }
    if (
        evidence_digests != snapshot.evidence_ref_digests
        or not required_evidence_ids <= evidence_ids
    ):
        return _invalid(
            ProofErrorCode.EVIDENCE_BINDING_MISMATCH,
            "proof evidence does not match the snapshot and clause references",
        )
    captured_at = parse_utc_second(snapshot.captured_at, "captured_at")
    for evidence in evidence_refs:
        observed_at = parse_utc_second(evidence.observed_at, "observed_at")
        valid_until = (
            parse_utc_second(evidence.valid_until, "valid_until")
            if evidence.valid_until is not None
            else None
        )
        if observed_at > captured_at or (valid_until is not None and captured_at >= valid_until):
            return _invalid(
                ProofErrorCode.EVIDENCE_STALE,
                f"evidence {evidence.evidence_id!r} was not valid at snapshot capture",
            )
    if authorization.policy_digest not in decision.policy_digests:
        return _invalid(
            ProofErrorCode.POLICY_BINDING_MISMATCH,
            "authorization policy is not bound by the decision",
        )
    replay_status = ReplayStatus.NOT_CHECKED
    history_status = HistoryStatus.UNANCHORED
    if context is not None and context.replay is not None:
        replay_status, replay_error = evaluate_replay(authorization, context.replay)
        if replay_status is not ReplayStatus.FRESH:
            return _invalid(
                ProofErrorCode.REPLAY_INVALID,
                replay_error or "replay context rejected the proof",
                replay_status=replay_status,
            )
    if context is not None and context.anchor is not None:
        anchor_context = context.anchor
        expected_head = AnchorHead(
            stream_id=receipt.stream_id,
            sequence=receipt.sequence,
            receipt_envelope_digest=sha256_digest(bundle.receipt_envelope_bytes),
        )
        anchored = False
        if anchor_context.store is not None:
            try:
                retained_head = anchor_context.store.trusted_head(receipt.stream_id)
            except VeridianError as exc:
                return _invalid(
                    ProofErrorCode.ANCHOR_INVALID,
                    str(exc),
                    replay_status=replay_status,
                    history_status=HistoryStatus.MISMATCH,
                )
            except Exception as exc:
                return _invalid(
                    ProofErrorCode.ANCHOR_INVALID,
                    f"anchor store failed: {type(exc).__name__}",
                    replay_status=replay_status,
                    history_status=HistoryStatus.MISMATCH,
                )
            if retained_head != expected_head:
                return _invalid(
                    ProofErrorCode.ANCHOR_INVALID,
                    "proof head does not match the independently retained head",
                    replay_status=replay_status,
                    history_status=HistoryStatus.MISMATCH,
                )
            anchored = True
        witnesses_valid, witness_key_ids, witness_error = validate_witnesses(
            bundle.witness_envelope_bytes,
            expected=expected_head,
            context=anchor_context,
        )
        if not witnesses_valid:
            return _invalid(
                ProofErrorCode.WITNESS_INVALID,
                witness_error or "witness validation failed",
                replay_status=replay_status,
                history_status=HistoryStatus.MISMATCH,
            )
        witnessed = bool(witness_key_ids)
        if anchored and witnessed:
            history_status = HistoryStatus.ANCHORED_AND_WITNESSED
        elif anchored:
            history_status = HistoryStatus.ANCHORED
        elif witnessed:
            history_status = HistoryStatus.WITNESSED
    return ProofVerification(
        valid=True,
        error_code=None,
        error=None,
        decision_digest=decision_digest,
        receipt_id=receipt.receipt_id,
        verified_signer_ids=envelope.verified_key_ids,
        replay_status=replay_status,
        history_status=history_status,
    )
