from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AnchorContext,
    AnchorHead,
    AssuranceValidationError,
    AssuranceVerificationError,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    Ed25519Signer,
    EvidenceRef,
    EvidenceTrust,
    HistoryStatus,
    NonceStatus,
    ProofBundleV1,
    ProofErrorCode,
    ProofVerificationContext,
    ReceiptStatementV1,
    ReplayContext,
    ReplayStatus,
    StaticKeyProvider,
    TransportBinding,
    VerificationSnapshotV1,
    VerifierExecutionMode,
    VerifierManifestV1,
    WitnessStatementV1,
    decode_profile_v1,
    encode_profile_v1,
    sha256_digest,
    sign_attestation,
    sign_receipt,
    sign_witness,
    verify_attestation,
    verify_proof_bundle,
)

_RFC8032_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
_RFC8032_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_RFC8032_EMPTY_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def _signer() -> Ed25519Signer:
    return Ed25519Signer.from_private_bytes("receipt-key-2026-08", _RFC8032_SEED)


def _proof() -> tuple[ProofBundleV1, StaticKeyProvider]:
    action = ActionSemanticsV1(
        action_type="bank.transfer",
        target="account:merchant-42",
        parameters={"amount_minor": 125_000, "currency": "USD"},
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="nonce-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest="sha256:" + "a" * 64,
        policy_digest="sha256:" + "b" * 64,
    )
    transport = TransportBinding(
        adapter_id="mcp-gateway",
        adapter_version="2.1.0",
        protocol="mcp",
        protocol_version="2026-07-28",
        message_id="req-991",
        raw_message_digest="sha256:" + "c" * 64,
    )
    contract_bytes = b'{"contract_id":"bank-payment-v1","schema_id":"example.contract.v1"}'
    manifest = VerifierManifestV1(
        verifier_id="bank.double-entry",
        semantic_version="1.3.0",
        build_digest="sha256:" + "d" * 64,
        config={"currency": "USD"},
        input_schema_digest="sha256:" + "e" * 64,
        output_schema_digest="sha256:" + "f" * 64,
        deterministic=True,
        execution_mode=VerifierExecutionMode.TRUSTED_IN_PROCESS,
        required_capabilities=(),
        resource_limits={"cpu_ms": 200},
    )
    evidence = EvidenceRef(
        evidence_id="ev_0123456789abcdef",
        schema_id="bank.sanctions.v2",
        media_type="application/json",
        producer_id="service:sanctions-prod",
        observed_at="2026-08-19T09:59:00Z",
        valid_until="2026-08-19T10:09:00Z",
        trust=EvidenceTrust.AUTHORITATIVE,
        tenant_id="tenant:acme",
        purpose="payment-screening",
        access_class="restricted",
        retention_class="aml-7y",
    )
    snapshot = VerificationSnapshotV1(
        authorization_envelope_digest=authorization.digest,
        state_digest=authorization.state_digest,
        evidence_ref_digests=(evidence.digest,),
        verifier_manifest_digests=(manifest.digest,),
        captured_at="2026-08-19T10:00:01Z",
    )
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=sha256_digest(contract_bytes),
        snapshot_digest=snapshot.digest,
        clause_results=(
            ClauseResultV1(
                clause_id="double-entry",
                severity=ClauseSeverity.HARD,
                status=ClauseStatus.SATISFIED,
                reason_code="BALANCED",
                verifier_manifest_digest=manifest.digest,
                evidence_ids=(evidence.evidence_id,),
                details={"currency": "USD"},
            ),
        ),
        policy_digests=(authorization.policy_digest,),
        verifier_manifest_digests=(manifest.digest,),
    )
    statement = ReceiptStatementV1(
        decision_digest=decision.digest,
        receipt_id="receipt-9001",
        issued_at="2026-08-19T10:00:03Z",
        sequence=41,
        deployment_id="veridian-bank-prod-1",
        transport_binding_digest=transport.digest,
        stream_id="tenant-acme-payments",
        previous_receipt_digest="sha256:" + "0" * 64,
    )
    signer = _signer()
    bundle = ProofBundleV1(
        semantic_bytes=action.to_bytes(),
        authorization_envelope_bytes=authorization.to_bytes(),
        contract_bytes=contract_bytes,
        snapshot_bytes=snapshot.to_bytes(),
        transport_binding_bytes=transport.to_bytes(),
        verifier_manifest_bytes=(manifest.to_bytes(),),
        evidence_ref_bytes=(evidence.to_bytes(),),
        decision_bytes=decision.to_bytes(),
        receipt_envelope_bytes=sign_receipt(statement, signer),
    )
    return bundle, StaticKeyProvider.from_signers(signer)


def test_ed25519_reference_matches_rfc8032_vector_one() -> None:
    signer = _signer()

    assert signer.public_key_bytes == _RFC8032_PUBLIC
    assert signer.sign(b"") == _RFC8032_EMPTY_SIGNATURE


def test_receipt_statement_has_an_exact_versioned_encoding() -> None:
    statement = ReceiptStatementV1(
        decision_digest="sha256:" + "1" * 64,
        receipt_id="receipt-9001",
        issued_at="2026-08-19T10:00:03Z",
        sequence=41,
        deployment_id="veridian-bank-prod-1",
        transport_binding_digest="sha256:" + "2" * 64,
        stream_id="tenant-acme-payments",
        previous_receipt_digest="sha256:" + "0" * 64,
    )

    expected = (
        b'{"decision_digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111",'
        b'"deployment_id":"veridian-bank-prod-1","issued_at":"2026-08-19T10:00:03Z",'
        b'"previous_receipt_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000",'
        b'"receipt_id":"receipt-9001","schema_id":"veridian.receipt-statement.v1",'
        b'"sequence":41,"stream_id":"tenant-acme-payments",'
        b'"transport_binding_digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222"}'
    )
    assert statement.to_bytes() == expected
    assert ReceiptStatementV1.from_bytes(expected) == statement


def test_proof_bundle_round_trips_and_verifies_offline() -> None:
    bundle, keys = _proof()

    restored = ProofBundleV1.from_bytes(bundle.to_bytes())
    verification = verify_proof_bundle(restored, keys)

    assert restored == bundle
    assert verification.valid
    assert verification.error_code is None
    assert verification.decision_digest == sha256_digest(bundle.decision_bytes)
    assert verification.verified_signer_ids == ("receipt-key-2026-08",)
    assert verification.replay_status is ReplayStatus.NOT_CHECKED
    assert verification.history_status is HistoryStatus.UNANCHORED


def test_exact_decision_byte_mutation_is_detected_offline() -> None:
    bundle, keys = _proof()

    verification = verify_proof_bundle(
        replace(bundle, decision_bytes=bundle.decision_bytes + b" "), keys
    )

    assert not verification.valid
    assert verification.error_code is ProofErrorCode.DECISION_DIGEST_MISMATCH


def test_receipt_signature_is_checked_before_payload_is_parsed() -> None:
    bundle, keys = _proof()
    envelope = dict(decode_profile_v1(bundle.receipt_envelope_bytes))
    envelope["payload"] = base64.b64encode(b"not-json").decode("ascii")
    tampered = replace(bundle, receipt_envelope_bytes=encode_profile_v1(envelope))

    verification = verify_proof_bundle(tampered, keys)

    assert not verification.valid
    assert verification.error_code is ProofErrorCode.SIGNATURE_INVALID


def test_substituted_transport_or_missing_manifest_is_detected() -> None:
    bundle, keys = _proof()

    transport_result = verify_proof_bundle(
        replace(bundle, transport_binding_bytes=b'{"schema_id":"other.transport.v1"}'), keys
    )
    manifest_result = verify_proof_bundle(replace(bundle, verifier_manifest_bytes=()), keys)

    assert transport_result.error_code is ProofErrorCode.TRANSPORT_BINDING_MISMATCH
    assert manifest_result.error_code is ProofErrorCode.VERIFIER_MANIFEST_MISMATCH


def test_internally_bound_but_stale_evidence_is_rejected() -> None:
    bundle, keys = _proof()
    stale_evidence = replace(
        EvidenceRef.from_bytes(bundle.evidence_ref_bytes[0]),
        valid_until="2026-08-19T10:00:01Z",
    )
    snapshot = replace(
        VerificationSnapshotV1.from_bytes(bundle.snapshot_bytes),
        evidence_ref_digests=(stale_evidence.digest,),
    )
    decision = replace(
        DecisionPayloadV1.from_bytes(bundle.decision_bytes),
        snapshot_digest=snapshot.digest,
    )
    receipt_payload = verify_attestation(
        bundle.receipt_envelope_bytes,
        expected_payload_type="application/vnd.veridian.receipt-statement.v1+json",
        keys=keys,
    ).payload
    receipt = replace(
        ReceiptStatementV1.from_bytes(receipt_payload),
        decision_digest=decision.digest,
    )
    stale_bundle = replace(
        bundle,
        evidence_ref_bytes=(stale_evidence.to_bytes(),),
        snapshot_bytes=snapshot.to_bytes(),
        decision_bytes=decision.to_bytes(),
        receipt_envelope_bytes=sign_receipt(receipt, _signer()),
    )

    verification = verify_proof_bundle(stale_bundle, keys)

    assert not verification.valid
    assert verification.error_code is ProofErrorCode.EVIDENCE_STALE


def test_signing_is_explicit_and_has_no_fallback_key() -> None:
    statement = ReceiptStatementV1(
        decision_digest="sha256:" + "1" * 64,
        receipt_id="receipt-9001",
        issued_at="2026-08-19T10:00:03Z",
        sequence=41,
        deployment_id="prod",
        transport_binding_digest="sha256:" + "2" * 64,
        stream_id="payments",
        previous_receipt_digest=None,
    )

    with pytest.raises(AssuranceValidationError, match="explicit signer"):
        sign_receipt(statement, None)


def test_generic_exact_byte_attestation_seam_enforces_payload_type() -> None:
    signer = _signer()
    keys = StaticKeyProvider.from_signers(signer)
    payload = b'{"permit_id":"permit-41"}'

    envelope = sign_attestation("application/vnd.veridian.permit.v1+json", payload, signer)
    verified = verify_attestation(
        envelope,
        expected_payload_type="application/vnd.veridian.permit.v1+json",
        keys=keys,
    )

    assert verified.payload == payload
    assert verified.verified_key_ids == (signer.key_id,)
    with pytest.raises(AssuranceVerificationError, match="payload type"):
        verify_attestation(
            envelope,
            expected_payload_type="application/vnd.veridian.effect-receipt.v1+json",
            keys=keys,
        )


class FixedNonceRegistry:
    def __init__(self, status: NonceStatus) -> None:
        self._status = status

    def status(self, *, audience: str, nonce: str) -> NonceStatus:
        assert audience == "bank-executor:prod"
        assert nonce == "nonce-0123456789abcdef"
        return self._status


def _replay_context(status: NonceStatus = NonceStatus.FRESH) -> ReplayContext:
    return ReplayContext(
        expected_audience="bank-executor:prod",
        expected_principal_id="agent:treasury-7",
        expected_state_digest="sha256:" + "a" * 64,
        now=datetime(2026, 8, 19, 10, 1, tzinfo=UTC),
        nonce_registry=FixedNonceRegistry(status),
    )


def test_contextual_replay_accepts_fresh_and_rejects_redeemed_nonce() -> None:
    bundle, keys = _proof()

    fresh = verify_proof_bundle(
        bundle,
        keys,
        context=ProofVerificationContext(replay=_replay_context()),
    )
    replayed = verify_proof_bundle(
        bundle,
        keys,
        context=ProofVerificationContext(replay=_replay_context(NonceStatus.REDEEMED)),
    )

    assert fresh.valid
    assert fresh.replay_status is ReplayStatus.FRESH
    assert not replayed.valid
    assert replayed.error_code is ProofErrorCode.REPLAY_INVALID
    assert replayed.replay_status is ReplayStatus.REDEEMED


def test_contextual_replay_rejects_wrong_audience_state_principal_and_time() -> None:
    bundle, keys = _proof()
    valid = _replay_context()
    cases = (
        replace(valid, expected_audience="bank-executor:staging"),
        replace(valid, expected_state_digest="sha256:" + "9" * 64),
        replace(valid, expected_principal_id="agent:other"),
        replace(valid, now=datetime(2026, 8, 19, 10, 6, tzinfo=UTC)),
    )

    statuses = [
        verify_proof_bundle(
            bundle,
            keys,
            context=ProofVerificationContext(replay=context),
        ).replay_status
        for context in cases
    ]

    assert statuses == [
        ReplayStatus.WRONG_AUDIENCE,
        ReplayStatus.STATE_MISMATCH,
        ReplayStatus.WRONG_PRINCIPAL,
        ReplayStatus.EXPIRED,
    ]


class FixedAnchorStore:
    def __init__(self, head: AnchorHead | None) -> None:
        self._head = head

    def trusted_head(self, stream_id: str) -> AnchorHead | None:
        assert stream_id == "tenant-acme-payments"
        return self._head


def test_independently_retained_head_detects_fork_or_rollback() -> None:
    bundle, keys = _proof()
    exact_head = AnchorHead(
        stream_id="tenant-acme-payments",
        sequence=41,
        receipt_envelope_digest=sha256_digest(bundle.receipt_envelope_bytes),
    )
    anchored = verify_proof_bundle(
        bundle,
        keys,
        context=ProofVerificationContext(anchor=AnchorContext(store=FixedAnchorStore(exact_head))),
    )
    forked = verify_proof_bundle(
        bundle,
        keys,
        context=ProofVerificationContext(
            anchor=AnchorContext(
                store=FixedAnchorStore(
                    replace(exact_head, receipt_envelope_digest="sha256:" + "8" * 64)
                )
            )
        ),
    )

    assert anchored.valid
    assert anchored.history_status is HistoryStatus.ANCHORED
    assert not forked.valid
    assert forked.error_code is ProofErrorCode.ANCHOR_INVALID
    assert forked.history_status is HistoryStatus.MISMATCH


def test_independent_witness_signature_binds_the_receipt_head() -> None:
    bundle, keys = _proof()
    witness_signer = Ed25519Signer.from_private_bytes("witness-a", bytes(range(32)))
    statement = WitnessStatementV1(
        stream_id="tenant-acme-payments",
        sequence=41,
        receipt_envelope_digest=sha256_digest(bundle.receipt_envelope_bytes),
        observed_at="2026-08-19T10:00:04Z",
    )
    witnessed_bundle = replace(
        bundle,
        witness_envelope_bytes=(sign_witness(statement, witness_signer),),
    )

    verified = verify_proof_bundle(
        witnessed_bundle,
        keys,
        context=ProofVerificationContext(
            anchor=AnchorContext(
                witness_keys=StaticKeyProvider.from_signers(witness_signer),
                minimum_witnesses=1,
            )
        ),
    )

    assert verified.valid
    assert verified.history_status is HistoryStatus.WITNESSED
