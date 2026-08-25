from __future__ import annotations

import threading
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
    Ed25519Signer,
    StaticKeyProvider,
    sha256_digest,
)
from veridian.effects import (
    DispatchRequest,
    DispatchResult,
    EffectExecutionError,
    EffectReceiptType,
    ExecutionPermitV1,
    PermitError,
    SqlitePermitStore,
    TrustedExecutor,
    sign_execution_permit,
    verify_effect_receipt,
)

_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
_STATE = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_MANIFEST = "sha256:" + "7" * 64


def _authorized_action() -> tuple[ActionSemanticsV1, ExecutionPermitV1, Ed25519Signer]:
    semantics = ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-42",
        {"amount_minor": 12_500_000, "currency": "USD"},
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=semantics.digest,
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
        contract_digest="sha256:" + "c" * 64,
        snapshot_digest=_STATE,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )
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
    signer = Ed25519Signer.from_private_bytes("executor-key-2026-08", _SEED)
    return semantics, permit, signer


class IdempotentBankRail:
    """Synthetic trusted adapter that models a rail honoring idempotency keys."""

    adapter_id = "synthetic-rtgs-v1"

    def __init__(self, *, crash_once_after_commit: bool = False) -> None:
        self.crash_once_after_commit = crash_once_after_commit
        self.attempts = 0
        self.economic_effects = 0
        self._results: dict[str, DispatchResult] = {}
        self._lock = threading.Lock()

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        with self._lock:
            self.attempts += 1
            result = self._results.get(request.idempotency_key)
            if result is None:
                self.economic_effects += 1
                result = DispatchResult(
                    producer_id="bank-simulator:rtgs",
                    receipt_type=EffectReceiptType.COMMITTED,
                    observed_at="2026-08-19T10:00:14Z",
                    external_reference_digest=sha256_digest(b"RTGS-ACK-9001"),
                    result_digest=sha256_digest(request.payload + b"|committed"),
                )
                self._results[request.idempotency_key] = result
                if self.crash_once_after_commit:
                    self.crash_once_after_commit = False
                    raise RuntimeError("simulated executor crash after rail commit")
            return result


def _executor(
    path: Path,
    rail: IdempotentBankRail,
    signer: Ed25519Signer,
) -> TrustedExecutor:
    keys = StaticKeyProvider.from_signers(signer)
    return TrustedExecutor(
        audience="bank-executor:prod",
        store=SqlitePermitStore(path),
        permit_keys=keys,
        receipt_keys=keys,
        receipt_signer=signer,
        adapter=rail,
    )


def test_agent_to_permit_to_trusted_executor_to_receipt_is_replay_safe(tmp_path: Path) -> None:
    semantics, permit, signer = _authorized_action()
    signed_permit = sign_execution_permit(permit, signer)
    rail = IdempotentBankRail()
    executor = _executor(tmp_path / "effects.db", rail, signer)
    first = executor.execute(
        signed_permit=signed_permit,
        semantics=semantics,
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        executed_at="2026-08-19T10:00:10Z",
    )
    retry = executor.execute(
        signed_permit=signed_permit,
        semantics=semantics,
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        executed_at="2026-08-19T10:00:11Z",
    )

    assert rail.economic_effects == 1
    assert rail.attempts == 1
    assert first.receipt == retry.receipt
    assert retry.replayed is True
    verified = verify_effect_receipt(
        retry.receipt_envelope,
        keys=StaticKeyProvider.from_signers(signer),
    )
    assert verified.receipt.permit_digest == permit.digest
    assert verified.receipt.semantic_digest == semantics.digest


def test_crash_after_external_commit_recovers_original_outbox_without_new_effect(
    tmp_path: Path,
) -> None:
    semantics, permit, signer = _authorized_action()
    signed_permit = sign_execution_permit(permit, signer)
    rail = IdempotentBankRail(crash_once_after_commit=True)
    path = tmp_path / "effects.db"

    with pytest.raises(EffectExecutionError, match="adapter dispatch failed"):
        _executor(path, rail, signer).execute(
            signed_permit=signed_permit,
            semantics=semantics,
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            executed_at="2026-08-19T10:00:10Z",
        )

    assert len(SqlitePermitStore(path).pending_outbox()) == 1
    recovered = _executor(path, rail, signer).execute(
        signed_permit=signed_permit,
        semantics=semantics,
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        executed_at="2026-08-19T10:00:11Z",
    )

    assert recovered.receipt.receipt_type is EffectReceiptType.COMMITTED
    assert rail.economic_effects == 1
    assert rail.attempts == 2
    assert SqlitePermitStore(path).pending_outbox() == ()


def test_invalid_semantics_never_reaches_credential_holding_adapter(tmp_path: Path) -> None:
    semantics, permit, signer = _authorized_action()
    rail = IdempotentBankRail()
    changed = ActionSemanticsV1(
        semantics.action_type,
        semantics.target,
        {"amount_minor": 12_500_001, "currency": "USD"},
    )

    with pytest.raises(PermitError, match="semantic"):
        _executor(tmp_path / "effects.db", rail, signer).execute(
            signed_permit=sign_execution_permit(permit, signer),
            semantics=changed,
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            executed_at="2026-08-19T10:00:10Z",
        )

    assert rail.attempts == 0
    assert rail.economic_effects == 0


def test_concurrent_executors_preserve_one_economic_effect(tmp_path: Path) -> None:
    semantics, permit, signer = _authorized_action()
    signed_permit = sign_execution_permit(permit, signer)
    rail = IdempotentBankRail()
    executor = _executor(tmp_path / "effects.db", rail, signer)

    def execute(_: int) -> str:
        outcome = executor.execute(
            signed_permit=signed_permit,
            semantics=semantics,
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            executed_at="2026-08-19T10:00:10Z",
        )
        return outcome.receipt.digest

    with ThreadPoolExecutor(max_workers=16) as pool:
        receipt_digests = tuple(pool.map(execute, range(64)))

    assert len(set(receipt_digests)) == 1
    assert rail.economic_effects == 1
    assert SqlitePermitStore(tmp_path / "effects.db").redemption_count(permit.permit_id) == 1
