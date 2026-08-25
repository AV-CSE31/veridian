"""Fail-closed clause algebra and deterministic decision payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    freeze_mapping,
    require_digest,
    require_exact_fields,
    require_string,
    require_string_tuple,
    sha256_digest,
)
from ._errors import AssuranceValidationError

DECISION_SCHEMA_ID = "veridian.decision.v1"
DECISION_ALGORITHM_SUITE = "veridian.cjson-sha256.v1"


class ClauseStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    ERROR = "error"


class ClauseSeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class Disposition(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HOLD = "hold"


@dataclass(frozen=True)
class ClauseResultV1:
    """One deterministic clause outcome with machine-readable provenance."""

    clause_id: str
    severity: ClauseSeverity
    status: ClauseStatus
    reason_code: str
    verifier_manifest_digest: str
    evidence_ids: tuple[str, ...]
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        for field_name in ("clause_id", "reason_code"):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        if not isinstance(self.severity, ClauseSeverity):
            raise AssuranceValidationError("severity must be a ClauseSeverity value")
        if not isinstance(self.status, ClauseStatus):
            raise AssuranceValidationError("status must be a ClauseStatus value")
        object.__setattr__(
            self,
            "verifier_manifest_digest",
            require_digest(self.verifier_manifest_digest, "verifier_manifest_digest"),
        )
        object.__setattr__(
            self, "evidence_ids", require_string_tuple(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(self, "details", freeze_mapping(self.details, "details"))

    def to_dict(self) -> dict[str, object]:
        return {
            "clause_id": self.clause_id,
            "severity": self.severity.value,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "verifier_manifest_digest": self.verifier_manifest_digest,
            "evidence_ids": self.evidence_ids,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ClauseResultV1:
        fields = frozenset(
            {
                "clause_id",
                "severity",
                "status",
                "reason_code",
                "verifier_manifest_digest",
                "evidence_ids",
                "details",
            }
        )
        require_exact_fields(payload, fields, "ClauseResultV1")
        try:
            severity = ClauseSeverity(require_string(payload["severity"], "severity"))
            status = ClauseStatus(require_string(payload["status"], "status"))
        except ValueError as exc:
            raise AssuranceValidationError("unsupported clause severity or status") from exc
        details = payload["details"]
        if not isinstance(details, Mapping):
            raise AssuranceValidationError("clause details must be an object")
        return cls(
            clause_id=require_string(payload["clause_id"], "clause_id"),
            severity=severity,
            status=status,
            reason_code=require_string(payload["reason_code"], "reason_code"),
            verifier_manifest_digest=require_digest(
                payload["verifier_manifest_digest"], "verifier_manifest_digest"
            ),
            evidence_ids=require_string_tuple(payload["evidence_ids"], "evidence_ids"),
            details=cast(Mapping[str, object], details),
        )


def aggregate_disposition(results: Iterable[ClauseResultV1]) -> Disposition:
    """Aggregate clause results without allowing uncertainty to become permission."""
    hard_statuses = [result.status for result in results if result.severity is ClauseSeverity.HARD]
    if ClauseStatus.VIOLATED in hard_statuses:
        return Disposition.DENY
    if ClauseStatus.UNKNOWN in hard_statuses or ClauseStatus.ERROR in hard_statuses:
        return Disposition.HOLD
    return Disposition.ALLOW


@dataclass(frozen=True)
class DecisionPayloadV1:
    """Canonical logical decision; excludes all receipt-event metadata."""

    authorization_envelope_digest: str
    contract_digest: str
    snapshot_digest: str
    clause_results: tuple[ClauseResultV1, ...]
    disposition: Disposition
    policy_digests: tuple[str, ...]
    verifier_manifest_digests: tuple[str, ...]
    obligations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "authorization_envelope_digest",
            "contract_digest",
            "snapshot_digest",
        ):
            object.__setattr__(
                self, field_name, require_digest(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "clause_results", tuple(self.clause_results))
        if not self.clause_results:
            raise AssuranceValidationError("a decision must contain at least one clause result")
        if not all(isinstance(item, ClauseResultV1) for item in self.clause_results):
            raise AssuranceValidationError("clause_results must contain ClauseResultV1 values")
        clause_ids = [item.clause_id for item in self.clause_results]
        if len(set(clause_ids)) != len(clause_ids):
            raise AssuranceValidationError("clause IDs must be unique")
        expected = aggregate_disposition(self.clause_results)
        if not isinstance(self.disposition, Disposition) or self.disposition is not expected:
            raise AssuranceValidationError(
                f"disposition must be {expected.value!r} for the supplied hard clauses"
            )
        object.__setattr__(
            self,
            "policy_digests",
            tuple(require_digest(item, "policy_digests") for item in self.policy_digests),
        )
        object.__setattr__(
            self,
            "verifier_manifest_digests",
            tuple(
                require_digest(item, "verifier_manifest_digests")
                for item in self.verifier_manifest_digests
            ),
        )
        object.__setattr__(
            self, "obligations", require_string_tuple(self.obligations, "obligations")
        )
        if not self.policy_digests:
            raise AssuranceValidationError("policy_digests must not be empty")
        declared = set(self.verifier_manifest_digests)
        used = {result.verifier_manifest_digest for result in self.clause_results}
        if not used <= declared:
            raise AssuranceValidationError(
                "every clause verifier manifest must be bound by the decision"
            )

    @classmethod
    def decide(
        cls,
        *,
        authorization_envelope_digest: str,
        contract_digest: str,
        snapshot_digest: str,
        clause_results: tuple[ClauseResultV1, ...],
        policy_digests: tuple[str, ...],
        verifier_manifest_digests: tuple[str, ...],
        obligations: tuple[str, ...] = (),
    ) -> DecisionPayloadV1:
        """Construct a decision whose disposition is derived, never caller-weakened."""
        results = tuple(clause_results)
        return cls(
            authorization_envelope_digest=authorization_envelope_digest,
            contract_digest=contract_digest,
            snapshot_digest=snapshot_digest,
            clause_results=results,
            disposition=aggregate_disposition(results),
            policy_digests=policy_digests,
            verifier_manifest_digests=verifier_manifest_digests,
            obligations=obligations,
        )

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": DECISION_SCHEMA_ID,
                "hash_algorithm": "sha256",
                "algorithm_suite": DECISION_ALGORITHM_SUITE,
                "authorization_envelope_digest": self.authorization_envelope_digest,
                "contract_digest": self.contract_digest,
                "snapshot_digest": self.snapshot_digest,
                "clause_results": [result.to_dict() for result in self.clause_results],
                "disposition": self.disposition.value,
                "policy_digests": self.policy_digests,
                "verifier_manifest_digests": self.verifier_manifest_digests,
                "obligations": self.obligations,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> DecisionPayloadV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "hash_algorithm",
                "algorithm_suite",
                "authorization_envelope_digest",
                "contract_digest",
                "snapshot_digest",
                "clause_results",
                "disposition",
                "policy_digests",
                "verifier_manifest_digests",
                "obligations",
            }
        )
        require_exact_fields(payload, fields, "DecisionPayloadV1")
        if payload["schema_id"] != DECISION_SCHEMA_ID:
            raise AssuranceValidationError("unsupported decision schema")
        if payload["hash_algorithm"] != "sha256":
            raise AssuranceValidationError("unsupported decision hash algorithm")
        if payload["algorithm_suite"] != DECISION_ALGORITHM_SUITE:
            raise AssuranceValidationError("unsupported decision algorithm suite")
        raw_results = payload["clause_results"]
        if not isinstance(raw_results, (list, tuple)):
            raise AssuranceValidationError("clause_results must be an array")
        results: list[ClauseResultV1] = []
        for item in raw_results:
            if not isinstance(item, Mapping):
                raise AssuranceValidationError("each clause result must be an object")
            results.append(ClauseResultV1.from_dict(cast(Mapping[str, object], item)))
        try:
            disposition = Disposition(require_string(payload["disposition"], "disposition"))
        except ValueError as exc:
            raise AssuranceValidationError("unsupported decision disposition") from exc
        return cls(
            authorization_envelope_digest=require_digest(
                payload["authorization_envelope_digest"], "authorization_envelope_digest"
            ),
            contract_digest=require_digest(payload["contract_digest"], "contract_digest"),
            snapshot_digest=require_digest(payload["snapshot_digest"], "snapshot_digest"),
            clause_results=tuple(results),
            disposition=disposition,
            policy_digests=tuple(
                require_digest(item, "policy_digests")
                for item in require_string_tuple(payload["policy_digests"], "policy_digests")
            ),
            verifier_manifest_digests=tuple(
                require_digest(item, "verifier_manifest_digests")
                for item in require_string_tuple(
                    payload["verifier_manifest_digests"], "verifier_manifest_digests"
                )
            ),
            obligations=require_string_tuple(payload["obligations"], "obligations"),
        )
