from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
)
from veridian.effects import ExecutionPermitV1, PermitError

_CONTRACT = "sha256:" + "c" * 64
_SNAPSHOT = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_MANIFEST = "sha256:" + "7" * 64


def _authorization() -> AuthorizationEnvelope:
    action = ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-42",
        {"amount_minor": 12_500_000, "currency": "USD"},
    )
    return AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="authorization-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest=_SNAPSHOT,
        policy_digest=_POLICY,
    )


def _decision(
    authorization: AuthorizationEnvelope,
    status: ClauseStatus = ClauseStatus.SATISFIED,
) -> DecisionPayloadV1:
    clause = ClauseResultV1(
        clause_id="bank-controls",
        severity=ClauseSeverity.HARD,
        status=status,
        reason_code=f"BANK_CONTROLS_{status.value.upper()}",
        verifier_manifest_digest=_MANIFEST,
        evidence_ids=("ev_0123456789abcdef",),
        details={},
    )
    return DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=_CONTRACT,
        snapshot_digest=_SNAPSHOT,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )


def test_allow_decision_issues_exactly_bound_single_use_permit() -> None:
    authorization = _authorization()
    decision = _decision(authorization)

    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=decision,
        permit_id="permit_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )

    assert permit.authorization_envelope_digest == authorization.digest
    assert permit.semantic_digest == authorization.semantic_digest
    assert permit.decision_digest == decision.digest
    assert permit.audience == authorization.audience
    assert permit.principal_id == authorization.principal_id
    assert permit.policy_digest == authorization.policy_digest
    assert permit.state_digest == authorization.state_digest
    assert permit.max_uses == 1
    assert ExecutionPermitV1.from_bytes(permit.to_bytes()) == permit


@pytest.mark.parametrize(
    "status",
    [ClauseStatus.VIOLATED, ClauseStatus.UNKNOWN, ClauseStatus.ERROR],
)
def test_non_allow_decision_can_never_issue_permit(status: ClauseStatus) -> None:
    authorization = _authorization()

    with pytest.raises(PermitError, match="ALLOW"):
        ExecutionPermitV1.issue(
            authorization=authorization,
            decision=_decision(authorization, status),
            permit_id="permit_0123456789abcdef",
            nonce="permit-nonce-0123456789abcdef",
            idempotency_key="payment-PAY-9001",
            issued_at="2026-08-19T10:00:01Z",
            not_before="2026-08-19T10:00:01Z",
            expires_at="2026-08-19T10:02:00Z",
        )


def test_permit_cannot_outlive_or_switch_the_exact_authorization() -> None:
    authorization = _authorization()
    decision = _decision(authorization)

    with pytest.raises(PermitError, match="validity"):
        ExecutionPermitV1.issue(
            authorization=authorization,
            decision=decision,
            permit_id="permit_0123456789abcdef",
            nonce="permit-nonce-0123456789abcdef",
            idempotency_key="payment-PAY-9001",
            issued_at="2026-08-19T10:00:01Z",
            not_before="2026-08-19T10:00:01Z",
            expires_at="2026-08-19T10:06:00Z",
        )

    unrelated = replace(authorization, nonce="different-auth-0123456789abcdef")
    with pytest.raises(PermitError, match="exact authorization"):
        ExecutionPermitV1.issue(
            authorization=unrelated,
            decision=decision,
            permit_id="permit_0123456789abcdef",
            nonce="permit-nonce-0123456789abcdef",
            idempotency_key="payment-PAY-9001",
            issued_at="2026-08-19T10:00:01Z",
            not_before="2026-08-19T10:00:01Z",
            expires_at="2026-08-19T10:02:00Z",
        )


def test_critical_permit_field_mutation_changes_identity() -> None:
    authorization = _authorization()
    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=_decision(authorization),
        permit_id="permit_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )

    assert replace(permit, audience="bank-executor:staging").digest != permit.digest
    assert replace(permit, semantic_digest="sha256:" + "8" * 64).digest != permit.digest
