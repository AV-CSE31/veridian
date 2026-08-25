"""Canonical banking intents, policy, approvals, and control snapshots."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import cast

from veridian.assurance import (
    ActionSemanticsV1,
    decode_profile_v1,
    encode_profile_v1,
    sha256_digest,
)
from veridian.assurance._canonical import (
    require_digest,
    require_exact_fields,
    require_string,
    require_string_tuple,
)
from veridian.assurance._model import parse_utc_second

from ._errors import BankingValidationError

BANK_POLICY_SCHEMA_ID = "veridian.bank-policy.v1"
BANK_SNAPSHOT_SCHEMA_ID = "veridian.bank-control-snapshot.v1"

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_RAIL = re.compile(r"^[A-Z][A-Z0-9_-]{1,15}$")
_MAX_SAFE_INTEGER = 2**53 - 1


def _string(value: object, field_name: str) -> str:
    try:
        return require_string(value, field_name)
    except Exception as exc:
        raise BankingValidationError(str(exc)) from exc


def _digest(value: object, field_name: str) -> str:
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise BankingValidationError(str(exc)) from exc


def _nonnegative_int(value: object, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise BankingValidationError(f"{field_name} must be a non-negative canonical safe integer")
    return value


def _positive_int(value: object, field_name: str) -> int:
    result = _nonnegative_int(value, field_name)
    if result == 0:
        raise BankingValidationError(f"{field_name} must be positive")
    return result


def _currency(value: object) -> str:
    result = _string(value, "currency")
    if not _CURRENCY.fullmatch(result):
        raise BankingValidationError("currency must be an uppercase ISO 4217 code")
    return result


def _date(value: object, field_name: str) -> str:
    result = _string(value, field_name)
    try:
        date.fromisoformat(result)
    except ValueError as exc:
        raise BankingValidationError(f"{field_name} must be an ISO calendar date") from exc
    return result


@dataclass(frozen=True)
class BankPaymentIntentV1:
    """Exact industrial payment proposed by an untrusted treasury agent."""

    payment_id: str
    debtor_account_id: str
    creditor_account_id: str
    beneficiary_id: str
    amount_minor: int
    fee_minor: int
    currency: str
    value_date: str
    rail: str
    purpose: str

    def __post_init__(self) -> None:
        for field_name in (
            "payment_id",
            "debtor_account_id",
            "creditor_account_id",
            "beneficiary_id",
            "purpose",
        ):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        object.__setattr__(self, "amount_minor", _positive_int(self.amount_minor, "amount_minor"))
        object.__setattr__(self, "fee_minor", _nonnegative_int(self.fee_minor, "fee_minor"))
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "value_date", _date(self.value_date, "value_date"))
        rail = _string(self.rail, "rail")
        if not _RAIL.fullmatch(rail):
            raise BankingValidationError("rail must be a canonical uppercase identifier")
        object.__setattr__(self, "rail", rail)

    def to_action_semantics(self) -> ActionSemanticsV1:
        return ActionSemanticsV1(
            action_type="bank.payment.submit",
            target=self.creditor_account_id,
            parameters={
                "payment_id": self.payment_id,
                "debtor_account_id": self.debtor_account_id,
                "creditor_account_id": self.creditor_account_id,
                "beneficiary_id": self.beneficiary_id,
                "amount_minor": self.amount_minor,
                "fee_minor": self.fee_minor,
                "currency": self.currency,
                "value_date": self.value_date,
                "rail": self.rail,
                "purpose": self.purpose,
            },
        )

    def to_bytes(self) -> bytes:
        return self.to_action_semantics().to_bytes()

    @property
    def digest(self) -> str:
        return self.to_action_semantics().digest

    @classmethod
    def from_bytes(cls, data: bytes) -> BankPaymentIntentV1:
        semantics = ActionSemanticsV1.from_bytes(data)
        if semantics.action_type != "bank.payment.submit":
            raise BankingValidationError("unsupported banking action type")
        fields = frozenset(
            {
                "payment_id",
                "debtor_account_id",
                "creditor_account_id",
                "beneficiary_id",
                "amount_minor",
                "fee_minor",
                "currency",
                "value_date",
                "rail",
                "purpose",
            }
        )
        try:
            require_exact_fields(semantics.parameters, fields, "BankPaymentIntentV1")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        creditor_account_id = _string(
            semantics.parameters["creditor_account_id"], "creditor_account_id"
        )
        if creditor_account_id != semantics.target:
            raise BankingValidationError("creditor account does not match action target")
        return cls(
            payment_id=_string(semantics.parameters["payment_id"], "payment_id"),
            debtor_account_id=_string(
                semantics.parameters["debtor_account_id"], "debtor_account_id"
            ),
            creditor_account_id=creditor_account_id,
            beneficiary_id=_string(semantics.parameters["beneficiary_id"], "beneficiary_id"),
            amount_minor=_positive_int(semantics.parameters["amount_minor"], "amount_minor"),
            fee_minor=_nonnegative_int(semantics.parameters["fee_minor"], "fee_minor"),
            currency=_currency(semantics.parameters["currency"]),
            value_date=_date(semantics.parameters["value_date"], "value_date"),
            rail=_string(semantics.parameters["rail"], "rail"),
            purpose=_string(semantics.parameters["purpose"], "purpose"),
        )


@dataclass(frozen=True)
class BankApprovalV1:
    """A pre-verified approval bound to the exact payment intent."""

    approval_id: str
    approver_id: str
    role: str
    intent_digest: str
    approved_at: str
    expires_at: str

    def __post_init__(self) -> None:
        for field_name in ("approval_id", "approver_id", "role"):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        object.__setattr__(self, "intent_digest", _digest(self.intent_digest, "intent_digest"))
        try:
            approved = parse_utc_second(self.approved_at, "approved_at")
            expires = parse_utc_second(self.expires_at, "expires_at")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        if expires <= approved:
            raise BankingValidationError("approval expiry must follow approval time")

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approver_id": self.approver_id,
            "role": self.role,
            "intent_digest": self.intent_digest,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> BankApprovalV1:
        fields = frozenset(
            {
                "approval_id",
                "approver_id",
                "role",
                "intent_digest",
                "approved_at",
                "expires_at",
            }
        )
        try:
            require_exact_fields(payload, fields, "BankApprovalV1")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        return cls(
            approval_id=_string(payload["approval_id"], "approval_id"),
            approver_id=_string(payload["approver_id"], "approver_id"),
            role=_string(payload["role"], "role"),
            intent_digest=_digest(payload["intent_digest"], "intent_digest"),
            approved_at=_string(payload["approved_at"], "approved_at"),
            expires_at=_string(payload["expires_at"], "expires_at"),
        )


@dataclass(frozen=True)
class BankPolicyV1:
    """Reviewed deterministic policy for one currency and payment rail scope."""

    policy_id: str
    policy_version: str
    currency: str
    per_payment_limit_minor: int
    rolling_limit_minor: int
    liquidity_buffer_minor: int
    high_value_threshold_minor: int
    standard_approval_quorum: int
    high_value_approval_quorum: int
    eligible_approval_roles: tuple[str, ...]
    allowed_beneficiaries: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _string(self.policy_id, "policy_id"))
        object.__setattr__(self, "policy_version", _string(self.policy_version, "policy_version"))
        object.__setattr__(self, "currency", _currency(self.currency))
        for field_name in (
            "per_payment_limit_minor",
            "rolling_limit_minor",
            "high_value_threshold_minor",
            "standard_approval_quorum",
            "high_value_approval_quorum",
        ):
            object.__setattr__(
                self, field_name, _positive_int(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "liquidity_buffer_minor",
            _nonnegative_int(self.liquidity_buffer_minor, "liquidity_buffer_minor"),
        )
        try:
            roles = tuple(sorted(require_string_tuple(self.eligible_approval_roles, "roles")))
            beneficiaries = tuple(
                sorted(require_string_tuple(self.allowed_beneficiaries, "beneficiaries"))
            )
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        if not roles or len(set(roles)) != len(roles):
            raise BankingValidationError("eligible approval roles must be non-empty and unique")
        if not beneficiaries or len(set(beneficiaries)) != len(beneficiaries):
            raise BankingValidationError("allowed beneficiaries must be non-empty and unique")
        if self.high_value_approval_quorum < self.standard_approval_quorum:
            raise BankingValidationError("high-value quorum cannot weaken standard quorum")
        object.__setattr__(self, "eligible_approval_roles", roles)
        object.__setattr__(self, "allowed_beneficiaries", beneficiaries)

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": BANK_POLICY_SCHEMA_ID,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "currency": self.currency,
                "per_payment_limit_minor": self.per_payment_limit_minor,
                "rolling_limit_minor": self.rolling_limit_minor,
                "liquidity_buffer_minor": self.liquidity_buffer_minor,
                "high_value_threshold_minor": self.high_value_threshold_minor,
                "standard_approval_quorum": self.standard_approval_quorum,
                "high_value_approval_quorum": self.high_value_approval_quorum,
                "eligible_approval_roles": self.eligible_approval_roles,
                "allowed_beneficiaries": self.allowed_beneficiaries,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())


@dataclass(frozen=True)
class BankControlSnapshotV1:
    """Authenticated point-in-time controls consumed by the pure banking gate."""

    evidence_id: str
    producer_id: str
    account_id: str
    ledger_version: int
    available_balance_minor: int
    rolling_outflow_minor: int
    pending_reserved_minor: int
    sanctions_clear: bool
    sanctions_subject: str
    approvals: tuple[BankApprovalV1, ...]
    observed_at: str
    valid_until: str

    def __post_init__(self) -> None:
        for field_name in ("evidence_id", "producer_id", "account_id", "sanctions_subject"):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        for field_name in (
            "ledger_version",
            "available_balance_minor",
            "rolling_outflow_minor",
            "pending_reserved_minor",
        ):
            object.__setattr__(
                self, field_name, _nonnegative_int(getattr(self, field_name), field_name)
            )
        if not isinstance(self.sanctions_clear, bool):
            raise BankingValidationError("sanctions_clear must be boolean")
        approvals = tuple(sorted(self.approvals, key=lambda approval: approval.approval_id))
        if not all(isinstance(item, BankApprovalV1) for item in approvals):
            raise BankingValidationError("approvals must contain BankApprovalV1 values")
        if len({item.approval_id for item in approvals}) != len(approvals):
            raise BankingValidationError("approval IDs must be unique")
        object.__setattr__(self, "approvals", approvals)
        try:
            observed = parse_utc_second(self.observed_at, "observed_at")
            valid = parse_utc_second(self.valid_until, "valid_until")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        if valid <= observed:
            raise BankingValidationError("snapshot valid_until must follow observed_at")

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": BANK_SNAPSHOT_SCHEMA_ID,
                "evidence_id": self.evidence_id,
                "producer_id": self.producer_id,
                "account_id": self.account_id,
                "ledger_version": self.ledger_version,
                "available_balance_minor": self.available_balance_minor,
                "rolling_outflow_minor": self.rolling_outflow_minor,
                "pending_reserved_minor": self.pending_reserved_minor,
                "sanctions_clear": self.sanctions_clear,
                "sanctions_subject": self.sanctions_subject,
                "approvals": [item.to_dict() for item in self.approvals],
                "observed_at": self.observed_at,
                "valid_until": self.valid_until,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> BankControlSnapshotV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "evidence_id",
                "producer_id",
                "account_id",
                "ledger_version",
                "available_balance_minor",
                "rolling_outflow_minor",
                "pending_reserved_minor",
                "sanctions_clear",
                "sanctions_subject",
                "approvals",
                "observed_at",
                "valid_until",
            }
        )
        try:
            require_exact_fields(payload, fields, "BankControlSnapshotV1")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        if payload["schema_id"] != BANK_SNAPSHOT_SCHEMA_ID:
            raise BankingValidationError("unsupported bank snapshot schema")
        raw_approvals = payload["approvals"]
        if not isinstance(raw_approvals, (list, tuple)):
            raise BankingValidationError("approvals must be an array")
        approvals: list[BankApprovalV1] = []
        for item in raw_approvals:
            if not isinstance(item, Mapping):
                raise BankingValidationError("each approval must be an object")
            approvals.append(BankApprovalV1.from_dict(cast(Mapping[str, object], item)))
        sanctions_clear = payload["sanctions_clear"]
        if not isinstance(sanctions_clear, bool):
            raise BankingValidationError("sanctions_clear must be boolean")
        return cls(
            evidence_id=_string(payload["evidence_id"], "evidence_id"),
            producer_id=_string(payload["producer_id"], "producer_id"),
            account_id=_string(payload["account_id"], "account_id"),
            ledger_version=_nonnegative_int(payload["ledger_version"], "ledger_version"),
            available_balance_minor=_nonnegative_int(
                payload["available_balance_minor"], "available_balance_minor"
            ),
            rolling_outflow_minor=_nonnegative_int(
                payload["rolling_outflow_minor"], "rolling_outflow_minor"
            ),
            pending_reserved_minor=_nonnegative_int(
                payload["pending_reserved_minor"], "pending_reserved_minor"
            ),
            sanctions_clear=sanctions_clear,
            sanctions_subject=_string(payload["sanctions_subject"], "sanctions_subject"),
            approvals=tuple(approvals),
            observed_at=_string(payload["observed_at"], "observed_at"),
            valid_until=_string(payload["valid_until"], "valid_until"),
        )
