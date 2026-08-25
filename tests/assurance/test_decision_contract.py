from __future__ import annotations

import pytest

from veridian.assurance import (
    AssuranceValidationError,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    Disposition,
    EvidenceRef,
    EvidenceTrust,
    aggregate_disposition,
)

_D1 = "sha256:" + "1" * 64
_D2 = "sha256:" + "2" * 64
_D3 = "sha256:" + "3" * 64
_D4 = "sha256:" + "4" * 64


def _clause(
    status: ClauseStatus,
    severity: ClauseSeverity = ClauseSeverity.HARD,
) -> ClauseResultV1:
    return ClauseResultV1(
        clause_id="sanctions-clear",
        severity=severity,
        status=status,
        reason_code=f"SANCTIONS_{status.value.upper()}",
        verifier_manifest_digest=_D4,
        evidence_ids=("ev_0123456789abcdef",),
        details={"list_version": "2026-08-19"},
    )


@pytest.mark.parametrize(
    ("clauses", "expected"),
    [
        ((_clause(ClauseStatus.SATISFIED),), Disposition.ALLOW),
        ((_clause(ClauseStatus.VIOLATED),), Disposition.DENY),
        ((_clause(ClauseStatus.UNKNOWN),), Disposition.HOLD),
        ((_clause(ClauseStatus.ERROR),), Disposition.HOLD),
        (
            (_clause(ClauseStatus.UNKNOWN), _clause(ClauseStatus.VIOLATED)),
            Disposition.DENY,
        ),
        ((_clause(ClauseStatus.ERROR, ClauseSeverity.SOFT),), Disposition.ALLOW),
    ],
)
def test_clause_algebra_is_fail_closed_for_hard_uncertainty(
    clauses: tuple[ClauseResultV1, ...], expected: Disposition
) -> None:
    assert aggregate_disposition(clauses) is expected


def test_decision_rejects_a_caller_supplied_disposition_that_weakens_controls() -> None:
    with pytest.raises(AssuranceValidationError):
        DecisionPayloadV1(
            authorization_envelope_digest=_D1,
            contract_digest=_D2,
            snapshot_digest=_D3,
            clause_results=(_clause(ClauseStatus.ERROR),),
            disposition=Disposition.ALLOW,
            policy_digests=(_D2,),
            verifier_manifest_digests=(_D4,),
        )


def test_decision_payload_has_stable_exact_bytes_and_digest() -> None:
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=_D1,
        contract_digest=_D2,
        snapshot_digest=_D3,
        clause_results=(_clause(ClauseStatus.SATISFIED),),
        policy_digests=(_D2,),
        verifier_manifest_digests=(_D4,),
    )

    expected = (
        b'{"algorithm_suite":"veridian.cjson-sha256.v1",'
        b'"authorization_envelope_digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111",'
        b'"clause_results":[{"clause_id":"sanctions-clear","details":{"list_version":"2026-08-19"},'
        b'"evidence_ids":["ev_0123456789abcdef"],"reason_code":"SANCTIONS_SATISFIED",'
        b'"severity":"hard","status":"satisfied",'
        b'"verifier_manifest_digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444"}],'
        b'"contract_digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222",'
        b'"disposition":"allow","hash_algorithm":"sha256","obligations":[],'
        b'"policy_digests":["sha256:2222222222222222222222222222222222222222222222222222222222222222"],'
        b'"schema_id":"veridian.decision.v1",'
        b'"snapshot_digest":"sha256:3333333333333333333333333333333333333333333333333333333333333333",'
        b'"verifier_manifest_digests":["sha256:4444444444444444444444444444444444444444444444444444444444444444"]}'
    )
    assert decision.to_bytes() == expected
    assert (
        decision.digest == "sha256:e96a56a31281a922a68aa5c51d5f181c49fa2942d1ef92af7e83341fea6028f8"
    )
    assert DecisionPayloadV1.from_bytes(expected) == decision


def test_evidence_ref_is_opaque_and_privacy_metadata_is_bound() -> None:
    evidence = EvidenceRef.new(
        schema_id="bank.sanctions-screen.v2",
        media_type="application/json",
        producer_id="service:sanctions-prod",
        observed_at="2026-08-19T09:59:00Z",
        valid_until="2026-08-19T10:09:00Z",
        trust=EvidenceTrust.AUTHORITATIVE,
        tenant_id="tenant:acme",
        purpose="payment-screening",
        access_class="restricted",
        retention_class="aml-7y",
        opaque_locator="opaque:c29tZS1lbmNyeXB0ZWQtdG9rZW4",
        commitment_scheme="hmac-sha256:v1",
        commitment="commitment:4e78c4f15cc0",
    )

    encoded = evidence.to_bytes()

    assert evidence.evidence_id.startswith("ev_")
    assert len(evidence.evidence_id) >= 35
    assert b"C:\\" not in encoded
    assert b"https://" not in encoded
    assert b"raw_payload" not in encoded
    assert EvidenceRef.from_bytes(encoded) == evidence


def test_evidence_ref_rejects_plaintext_locator() -> None:
    with pytest.raises(AssuranceValidationError):
        EvidenceRef.new(
            schema_id="test.v1",
            media_type="text/plain",
            producer_id="test",
            observed_at="2026-08-19T09:59:00Z",
            valid_until=None,
            trust=EvidenceTrust.UNVERIFIED,
            tenant_id="tenant:test",
            purpose="test",
            access_class="restricted",
            retention_class="delete-1d",
            opaque_locator="https://storage.example/secrets/customer.json",
        )


def test_evidence_ref_rejects_plaintext_disguised_with_opaque_prefix() -> None:
    with pytest.raises(AssuranceValidationError):
        EvidenceRef.new(
            schema_id="test.v1",
            media_type="text/plain",
            producer_id="test",
            observed_at="2026-08-19T09:59:00Z",
            valid_until=None,
            trust=EvidenceTrust.UNVERIFIED,
            tenant_id="tenant:test",
            purpose="test",
            access_class="restricted",
            retention_class="delete-1d",
            opaque_locator="opaque:https://storage.example/customer.json",
        )
