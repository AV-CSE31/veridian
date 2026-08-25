from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
)
from veridian.effects import (
    ExecutionPermitV1,
    OutboxStatus,
    PermitError,
    PermitReplayError,
    SqlitePermitStore,
)

_STATE = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_CONTRACT = "sha256:" + "c" * 64
_MANIFEST = "sha256:" + "7" * 64


def _permit() -> ExecutionPermitV1:
    action = ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-42",
        {"amount_minor": 12_500_000, "currency": "USD"},
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="authorization-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest=_STATE,
        policy_digest=_POLICY,
    )
    clause = ClauseResultV1(
        clause_id="bank-controls",
        severity=ClauseSeverity.HARD,
        status=ClauseStatus.SATISFIED,
        reason_code="BANK_CONTROLS_SATISFIED",
        verifier_manifest_digest=_MANIFEST,
        evidence_ids=("ev_0123456789abcdef",),
        details={},
    )
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=_CONTRACT,
        snapshot_digest=_STATE,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )
    return ExecutionPermitV1.issue(
        authorization=authorization,
        decision=decision,
        permit_id="permit_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )


def _redeem(store: SqlitePermitStore, permit: ExecutionPermitV1) -> str:
    return store.redeem(
        permit,
        audience="bank-executor:prod",
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        dispatch_payload=b'{"amount_minor":12500000,"currency":"USD"}',
        redeemed_at="2026-08-19T10:00:10Z",
    ).outbox_id


def test_redemption_and_outbox_creation_are_one_durable_transaction(tmp_path: Path) -> None:
    path = tmp_path / "effects.db"
    permit = _permit()
    store = SqlitePermitStore(path)
    store.register(permit)

    outbox = store.redeem(
        permit,
        audience="bank-executor:prod",
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        dispatch_payload=b'{"amount_minor":12500000,"currency":"USD"}',
        redeemed_at="2026-08-19T10:00:10Z",
    )

    assert outbox.permit_id == permit.permit_id
    assert outbox.idempotency_key == permit.idempotency_key
    assert outbox.status is OutboxStatus.PENDING

    # A fresh process can recover the exact committed dispatch intent.
    recovered = SqlitePermitStore(path).pending_outbox()
    assert recovered == (outbox,)


def test_concurrent_exact_retries_create_one_redemption_and_one_outbox(tmp_path: Path) -> None:
    store = SqlitePermitStore(tmp_path / "effects.db")
    permit = _permit()
    store.register(permit)

    with ThreadPoolExecutor(max_workers=16) as pool:
        outbox_ids = tuple(pool.map(lambda _: _redeem(store, permit), range(64)))

    assert len(set(outbox_ids)) == 1
    assert len(store.pending_outbox()) == 1
    assert store.redemption_count(permit.permit_id) == 1


def test_changed_replay_is_rejected_without_second_outbox(tmp_path: Path) -> None:
    store = SqlitePermitStore(tmp_path / "effects.db")
    permit = _permit()
    store.register(permit)
    _redeem(store, permit)

    with pytest.raises(PermitReplayError):
        store.redeem(
            permit,
            audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            dispatch_payload=b'{"amount_minor":12500001,"currency":"USD"}',
            redeemed_at="2026-08-19T10:00:11Z",
        )

    assert len(store.pending_outbox()) == 1


@pytest.mark.parametrize(
    ("audience", "state_digest", "policy_digest", "redeemed_at", "match"),
    [
        ("bank-executor:staging", _STATE, _POLICY, "2026-08-19T10:00:10Z", "audience"),
        ("bank-executor:prod", "sha256:" + "0" * 64, _POLICY, "2026-08-19T10:00:10Z", "state"),
        ("bank-executor:prod", _STATE, "sha256:" + "0" * 64, "2026-08-19T10:00:10Z", "policy"),
        ("bank-executor:prod", _STATE, _POLICY, "2026-08-19T10:03:00Z", "expired"),
    ],
)
def test_context_mismatch_or_expiry_fails_before_consumption(
    tmp_path: Path,
    audience: str,
    state_digest: str,
    policy_digest: str,
    redeemed_at: str,
    match: str,
) -> None:
    store = SqlitePermitStore(tmp_path / "effects.db")
    permit = _permit()
    store.register(permit)

    with pytest.raises(PermitError, match=match):
        store.redeem(
            permit,
            audience=audience,
            current_state_digest=state_digest,
            current_policy_digest=policy_digest,
            dispatch_payload=b"{}",
            redeemed_at=redeemed_at,
        )

    assert store.redemption_count(permit.permit_id) == 0
    assert store.pending_outbox() == ()


def test_revocation_is_fail_closed_and_idempotent(tmp_path: Path) -> None:
    store = SqlitePermitStore(tmp_path / "effects.db")
    permit = _permit()
    store.register(permit)

    store.revoke(
        permit.permit_id,
        reason="policy-replaced",
        revoked_at="2026-08-19T10:00:09Z",
    )
    store.revoke(
        permit.permit_id,
        reason="policy-replaced",
        revoked_at="2026-08-19T10:00:09Z",
    )

    with pytest.raises(PermitError, match="revoked"):
        _redeem(store, permit)


def test_outbox_completion_is_idempotent_but_conflicts_fail_closed(tmp_path: Path) -> None:
    store = SqlitePermitStore(tmp_path / "effects.db")
    permit = _permit()
    store.register(permit)
    outbox_id = _redeem(store, permit)
    response = "sha256:" + "a" * 64

    store.mark_dispatched(
        outbox_id,
        response_digest=response,
        dispatched_at="2026-08-19T10:00:12Z",
    )
    store.mark_dispatched(
        outbox_id,
        response_digest=response,
        dispatched_at="2026-08-19T10:00:12Z",
    )

    assert store.pending_outbox() == ()
    with pytest.raises(PermitError, match="conflicting"):
        store.mark_dispatched(
            outbox_id,
            response_digest="sha256:" + "b" * 64,
            dispatched_at="2026-08-19T10:00:13Z",
        )
