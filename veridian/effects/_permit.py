"""Canonical, single-use execution permits bound to assurance decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from veridian.assurance import (
    AuthorizationEnvelope,
    DecisionPayloadV1,
    Disposition,
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

from ._errors import PermitError

EXECUTION_PERMIT_SCHEMA_ID = "veridian.execution-permit.v1"
EXECUTION_PERMIT_SUITE = "veridian.cjson-sha256.v1"


def _string(value: object, field_name: str) -> str:
    try:
        return require_string(value, field_name)
    except Exception as exc:
        raise PermitError(str(exc)) from exc


def _digest(value: object, field_name: str) -> str:
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise PermitError(str(exc)) from exc


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    try:
        return require_string_tuple(value, field_name)
    except Exception as exc:
        raise PermitError(str(exc)) from exc


@dataclass(frozen=True)
class ExecutionPermitV1:
    """Unsigned permit payload; trust is supplied by a separate attestation."""

    permit_id: str
    semantic_digest: str
    authorization_envelope_digest: str
    decision_digest: str
    contract_digest: str
    policy_digest: str
    state_digest: str
    principal_id: str
    audience: str
    purpose: str
    nonce: str
    idempotency_key: str
    issued_at: str
    not_before: str
    expires_at: str
    obligations: tuple[str, ...]
    max_uses: Literal[1] = 1

    def __post_init__(self) -> None:
        for field_name in (
            "semantic_digest",
            "authorization_envelope_digest",
            "decision_digest",
            "contract_digest",
            "policy_digest",
            "state_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        for field_name in (
            "permit_id",
            "principal_id",
            "audience",
            "purpose",
            "nonce",
            "idempotency_key",
        ):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        if len(self.nonce) < 16:
            raise PermitError("nonce must contain at least 16 characters")
        object.__setattr__(self, "obligations", _strings(self.obligations, "obligations"))
        if self.max_uses != 1:
            raise PermitError("execution permits are single-use; max_uses must be 1")
        try:
            issued = parse_utc_second(self.issued_at, "issued_at")
            start = parse_utc_second(self.not_before, "not_before")
            end = parse_utc_second(self.expires_at, "expires_at")
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        if end <= start or issued >= end:
            raise PermitError("permit validity window is invalid")

    @classmethod
    def issue(
        cls,
        *,
        authorization: AuthorizationEnvelope,
        decision: DecisionPayloadV1,
        permit_id: str,
        nonce: str,
        idempotency_key: str,
        issued_at: str,
        not_before: str,
        expires_at: str,
    ) -> ExecutionPermitV1:
        """Derive a permit from one exact ALLOW decision and authorization."""

        if decision.disposition is not Disposition.ALLOW:
            raise PermitError("only an ALLOW decision can issue an execution permit")
        if authorization.semantic_kind != "action":
            raise PermitError("execution permits require action semantics")
        if decision.authorization_envelope_digest != authorization.digest:
            raise PermitError("decision does not bind the exact authorization envelope")
        if decision.snapshot_digest != authorization.state_digest:
            raise PermitError("decision snapshot does not match authorization state")
        if authorization.policy_digest not in decision.policy_digests:
            raise PermitError("decision does not bind the authorization policy")
        try:
            auth_start = parse_utc_second(authorization.not_before, "authorization.not_before")
            auth_end = parse_utc_second(authorization.expires_at, "authorization.expires_at")
            permit_issued = parse_utc_second(issued_at, "issued_at")
            permit_start = parse_utc_second(not_before, "not_before")
            permit_end = parse_utc_second(expires_at, "expires_at")
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        if not (
            auth_start <= permit_issued < auth_end
            and auth_start <= permit_start < permit_end <= auth_end
        ):
            raise PermitError("permit validity must be contained by authorization validity")
        return cls(
            permit_id=permit_id,
            semantic_digest=authorization.semantic_digest,
            authorization_envelope_digest=authorization.digest,
            decision_digest=decision.digest,
            contract_digest=decision.contract_digest,
            policy_digest=authorization.policy_digest,
            state_digest=authorization.state_digest,
            principal_id=authorization.principal_id,
            audience=authorization.audience,
            purpose=authorization.purpose,
            nonce=nonce,
            idempotency_key=idempotency_key,
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            obligations=decision.obligations,
        )

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": EXECUTION_PERMIT_SCHEMA_ID,
                "algorithm_suite": EXECUTION_PERMIT_SUITE,
                "permit_id": self.permit_id,
                "semantic_digest": self.semantic_digest,
                "authorization_envelope_digest": self.authorization_envelope_digest,
                "decision_digest": self.decision_digest,
                "contract_digest": self.contract_digest,
                "policy_digest": self.policy_digest,
                "state_digest": self.state_digest,
                "principal_id": self.principal_id,
                "audience": self.audience,
                "purpose": self.purpose,
                "nonce": self.nonce,
                "idempotency_key": self.idempotency_key,
                "issued_at": self.issued_at,
                "not_before": self.not_before,
                "expires_at": self.expires_at,
                "obligations": self.obligations,
                "max_uses": self.max_uses,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> ExecutionPermitV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "algorithm_suite",
                "permit_id",
                "semantic_digest",
                "authorization_envelope_digest",
                "decision_digest",
                "contract_digest",
                "policy_digest",
                "state_digest",
                "principal_id",
                "audience",
                "purpose",
                "nonce",
                "idempotency_key",
                "issued_at",
                "not_before",
                "expires_at",
                "obligations",
                "max_uses",
            }
        )
        try:
            require_exact_fields(payload, fields, "ExecutionPermitV1")
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        if payload["schema_id"] != EXECUTION_PERMIT_SCHEMA_ID:
            raise PermitError("unsupported execution permit schema")
        if payload["algorithm_suite"] != EXECUTION_PERMIT_SUITE:
            raise PermitError("unsupported execution permit algorithm suite")
        max_uses = payload["max_uses"]
        if max_uses != 1:
            raise PermitError("execution permits are single-use")
        return cls(
            permit_id=_string(payload["permit_id"], "permit_id"),
            semantic_digest=_digest(payload["semantic_digest"], "semantic_digest"),
            authorization_envelope_digest=_digest(
                payload["authorization_envelope_digest"],
                "authorization_envelope_digest",
            ),
            decision_digest=_digest(payload["decision_digest"], "decision_digest"),
            contract_digest=_digest(payload["contract_digest"], "contract_digest"),
            policy_digest=_digest(payload["policy_digest"], "policy_digest"),
            state_digest=_digest(payload["state_digest"], "state_digest"),
            principal_id=_string(payload["principal_id"], "principal_id"),
            audience=_string(payload["audience"], "audience"),
            purpose=_string(payload["purpose"], "purpose"),
            nonce=_string(payload["nonce"], "nonce"),
            idempotency_key=_string(payload["idempotency_key"], "idempotency_key"),
            issued_at=_string(payload["issued_at"], "issued_at"),
            not_before=_string(payload["not_before"], "not_before"),
            expires_at=_string(payload["expires_at"], "expires_at"),
            obligations=_strings(payload["obligations"], "obligations"),
            max_uses=max_uses,
        )
