"""Pure, fail-closed banking policy gate over authenticated snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from veridian.assurance import (
    AssuranceError,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    Signer,
    VerificationKeyProvider,
    encode_profile_v1,
    sha256_digest,
    sign_attestation,
    verify_attestation,
)
from veridian.assurance._model import parse_utc_second

from ._errors import BankingValidationError
from ._models import BankControlSnapshotV1, BankPaymentIntentV1, BankPolicyV1

BANK_SNAPSHOT_PAYLOAD_TYPE = "application/vnd.veridian.bank-control-snapshot.v1+json"
BANK_CONTRACT_BYTES = encode_profile_v1(
    {
        "schema_id": "veridian.bank-payment-contract.v1",
        "contract_id": "industrial-rtgs-assurance",
        "clause_algebra": "veridian.decision.v1",
    }
)
BANK_CONTRACT_DIGEST = sha256_digest(BANK_CONTRACT_BYTES)
BANK_VERIFIER_MANIFEST_DIGEST = sha256_digest(
    encode_profile_v1(
        {
            "schema_id": "veridian.verifier-manifest-lite.v1",
            "verifier_id": "bank.payment-controls",
            "semantic_version": "1.0.0",
            "deterministic": True,
            "arithmetic": "integer-minor-units",
        }
    )
)


def sign_bank_snapshot(snapshot: BankControlSnapshotV1, signer: Signer | None) -> bytes:
    if not isinstance(snapshot, BankControlSnapshotV1):
        raise BankingValidationError("sign_bank_snapshot requires BankControlSnapshotV1")
    try:
        return sign_attestation(BANK_SNAPSHOT_PAYLOAD_TYPE, snapshot.to_bytes(), signer)
    except AssuranceError as exc:
        raise BankingValidationError(f"bank snapshot attestation failed: {exc}") from exc


@dataclass(frozen=True)
class BankingEvaluation:
    """Decision plus the exact authenticated state used to derive it."""

    decision: DecisionPayloadV1
    snapshot: BankControlSnapshotV1


class BankingGate:
    """Industrial payment controls; it never acquires executor credentials."""

    @staticmethod
    def _clause(
        snapshot: BankControlSnapshotV1,
        clause_id: str,
        status: ClauseStatus,
        reason_code: str,
        details: dict[str, object],
    ) -> ClauseResultV1:
        return ClauseResultV1(
            clause_id=clause_id,
            severity=ClauseSeverity.HARD,
            status=status,
            reason_code=reason_code,
            verifier_manifest_digest=BANK_VERIFIER_MANIFEST_DIGEST,
            evidence_ids=(snapshot.evidence_id,),
            details=details,
        )

    @classmethod
    def evaluate(
        cls,
        *,
        intent: BankPaymentIntentV1,
        authorization: AuthorizationEnvelope,
        policy: BankPolicyV1,
        signed_snapshot: bytes,
        evidence_keys: VerificationKeyProvider,
        decision_at: str,
    ) -> BankingEvaluation:
        if not isinstance(intent, BankPaymentIntentV1):
            raise BankingValidationError("intent must be BankPaymentIntentV1")
        if not isinstance(authorization, AuthorizationEnvelope):
            raise BankingValidationError("authorization must be AuthorizationEnvelope")
        if not isinstance(policy, BankPolicyV1):
            raise BankingValidationError("policy must be BankPolicyV1")
        try:
            verified = verify_attestation(
                signed_snapshot,
                expected_payload_type=BANK_SNAPSHOT_PAYLOAD_TYPE,
                keys=evidence_keys,
            )
            snapshot = BankControlSnapshotV1.from_bytes(verified.payload)
            now = parse_utc_second(decision_at, "decision_at")
        except (AssuranceError, BankingValidationError) as exc:
            raise BankingValidationError(f"bank snapshot verification failed: {exc}") from exc
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc

        if (
            authorization.semantic_kind != "action"
            or authorization.semantic_digest != intent.digest
        ):
            raise BankingValidationError("authorization does not bind the exact payment intent")
        if authorization.policy_digest != policy.digest:
            raise BankingValidationError("authorization policy binding does not match")
        if authorization.state_digest != snapshot.digest:
            raise BankingValidationError("authorization state binding does not match")
        if policy.currency != intent.currency:
            raise BankingValidationError("policy currency does not match payment currency")
        if snapshot.account_id != intent.debtor_account_id:
            raise BankingValidationError("snapshot account does not match payment debtor")

        clauses: list[ClauseResultV1] = []
        beneficiary_ok = intent.beneficiary_id in policy.allowed_beneficiaries
        clauses.append(
            cls._clause(
                snapshot,
                "beneficiary-allowlist",
                ClauseStatus.SATISFIED if beneficiary_ok else ClauseStatus.VIOLATED,
                "BANK_BENEFICIARY_ALLOWED" if beneficiary_ok else "BANK_BENEFICIARY_DENIED",
                {"beneficiary_digest": sha256_digest(intent.beneficiary_id.encode("utf-8"))},
            )
        )

        sanctions_ok = snapshot.sanctions_clear and (
            snapshot.sanctions_subject == intent.beneficiary_id
        )
        sanctions_reason = (
            "BANK_SANCTIONS_CLEAR"
            if sanctions_ok
            else (
                "BANK_SANCTIONS_SUBJECT_MISMATCH"
                if snapshot.sanctions_subject != intent.beneficiary_id
                else "BANK_SANCTIONS_BLOCKED"
            )
        )
        clauses.append(
            cls._clause(
                snapshot,
                "sanctions-screen",
                ClauseStatus.SATISFIED if sanctions_ok else ClauseStatus.VIOLATED,
                sanctions_reason,
                {"producer_id": snapshot.producer_id},
            )
        )

        total_debit = intent.amount_minor + intent.fee_minor
        post_reservation_balance = (
            snapshot.available_balance_minor - snapshot.pending_reserved_minor - total_debit
        )
        funds_ok = post_reservation_balance >= policy.liquidity_buffer_minor
        clauses.append(
            cls._clause(
                snapshot,
                "available-funds",
                ClauseStatus.SATISFIED if funds_ok else ClauseStatus.VIOLATED,
                "BANK_FUNDS_SUFFICIENT" if funds_ok else "BANK_FUNDS_INSUFFICIENT",
                {
                    "post_reservation_balance_minor": post_reservation_balance,
                    "required_buffer_minor": policy.liquidity_buffer_minor,
                    "ledger_version": snapshot.ledger_version,
                },
            )
        )

        payment_limit_ok = total_debit <= policy.per_payment_limit_minor
        clauses.append(
            cls._clause(
                snapshot,
                "per-payment-limit",
                ClauseStatus.SATISFIED if payment_limit_ok else ClauseStatus.VIOLATED,
                "BANK_PAYMENT_LIMIT_OK" if payment_limit_ok else "BANK_PAYMENT_LIMIT_EXCEEDED",
                {
                    "total_debit_minor": total_debit,
                    "limit_minor": policy.per_payment_limit_minor,
                },
            )
        )

        aggregate = snapshot.rolling_outflow_minor + snapshot.pending_reserved_minor + total_debit
        rolling_ok = aggregate <= policy.rolling_limit_minor
        clauses.append(
            cls._clause(
                snapshot,
                "rolling-exposure-limit",
                ClauseStatus.SATISFIED if rolling_ok else ClauseStatus.VIOLATED,
                "BANK_ROLLING_LIMIT_OK" if rolling_ok else "BANK_ROLLING_LIMIT_EXCEEDED",
                {"aggregate_minor": aggregate, "limit_minor": policy.rolling_limit_minor},
            )
        )

        eligible = [
            approval
            for approval in snapshot.approvals
            if approval.role in policy.eligible_approval_roles
            and parse_utc_second(approval.approved_at, "approval.approved_at")
            <= now
            < parse_utc_second(approval.expires_at, "approval.expires_at")
        ]
        matching = [approval for approval in eligible if approval.intent_digest == intent.digest]
        required_quorum = (
            policy.high_value_approval_quorum
            if total_debit >= policy.high_value_threshold_minor
            else policy.standard_approval_quorum
        )
        approval_ids = {approval.approver_id for approval in matching}
        if eligible and not matching:
            approval_status = ClauseStatus.VIOLATED
            approval_reason = "BANK_APPROVAL_INTENT_MISMATCH"
        elif len(approval_ids) != len(matching):
            approval_status = ClauseStatus.VIOLATED
            approval_reason = "BANK_APPROVER_DUPLICATED"
        elif len(approval_ids) < required_quorum:
            approval_status = ClauseStatus.UNKNOWN
            approval_reason = "BANK_APPROVAL_QUORUM_MISSING"
        else:
            approval_status = ClauseStatus.SATISFIED
            approval_reason = "BANK_APPROVAL_QUORUM_MET"
        clauses.append(
            cls._clause(
                snapshot,
                "approval-quorum",
                approval_status,
                approval_reason,
                {"distinct_approvers": len(approval_ids), "required_quorum": required_quorum},
            )
        )

        distinct_roles = {approval.role for approval in matching}
        principal_is_approver = authorization.principal_id in approval_ids
        sod_ok = (
            len(approval_ids) == len(matching)
            and not principal_is_approver
            and len(distinct_roles) >= min(required_quorum, len(policy.eligible_approval_roles))
        )
        if approval_status is not ClauseStatus.SATISFIED:
            sod_status = ClauseStatus.UNKNOWN
            sod_reason = "BANK_SEPARATION_OF_DUTIES_UNDETERMINED"
        else:
            sod_status = ClauseStatus.SATISFIED if sod_ok else ClauseStatus.VIOLATED
            sod_reason = (
                "BANK_SEPARATION_OF_DUTIES_MET" if sod_ok else "BANK_SEPARATION_OF_DUTIES_VIOLATED"
            )
        clauses.append(
            cls._clause(
                snapshot,
                "separation-of-duties",
                sod_status,
                sod_reason,
                {
                    "distinct_roles": len(distinct_roles),
                    "principal_is_approver": principal_is_approver,
                },
            )
        )

        observed = parse_utc_second(snapshot.observed_at, "snapshot.observed_at")
        valid_until = parse_utc_second(snapshot.valid_until, "snapshot.valid_until")
        fresh = observed <= now <= valid_until
        clauses.append(
            cls._clause(
                snapshot,
                "evidence-freshness",
                ClauseStatus.SATISFIED if fresh else ClauseStatus.UNKNOWN,
                "BANK_EVIDENCE_FRESH" if fresh else "BANK_EVIDENCE_STALE",
                {"observed_at": snapshot.observed_at, "valid_until": snapshot.valid_until},
            )
        )

        decision = DecisionPayloadV1.decide(
            authorization_envelope_digest=authorization.digest,
            contract_digest=BANK_CONTRACT_DIGEST,
            snapshot_digest=snapshot.digest,
            clause_results=tuple(clauses),
            policy_digests=(policy.digest,),
            verifier_manifest_digests=(BANK_VERIFIER_MANIFEST_DIGEST,),
        )
        return BankingEvaluation(decision=decision, snapshot=snapshot)
