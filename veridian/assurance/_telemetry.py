"""Dependency-free OpenTelemetry semantic export for assurance linkages.

The mapping is intentionally small and versioned.  It exports cryptographic
digests and bounded lifecycle statuses only: never semantic parameters,
principals, business identifiers, effect IDs, provider references, or receipt
payloads.  Callers provide an existing Span-like object; this module performs
no global tracer-provider or SDK setup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from ._canonical import require_digest, sha256_digest
from ._decision import DecisionPayloadV1
from ._errors import AssuranceValidationError

if TYPE_CHECKING:
    from veridian.effects import EffectEventV1, EffectReceiptV1, ExecutionPermitV1


OTEL_SEMCONV_SCHEMA_V1 = "veridian.otel-semconv.v1"
TelemetryAttributeValue: TypeAlias = bool | int | float | str


class TelemetryStage(StrEnum):
    """Ordered assurance milestones represented by the v1 semantic mapping."""

    DECISION = "decision"
    PERMIT = "permit"
    EXECUTION = "execution"
    RECEIPT = "receipt"


_EVENT_NAMES: Mapping[TelemetryStage, str] = {
    TelemetryStage.DECISION: "veridian.assurance.decision",
    TelemetryStage.PERMIT: "veridian.assurance.permit",
    TelemetryStage.EXECUTION: "veridian.assurance.execution",
    TelemetryStage.RECEIPT: "veridian.assurance.receipt",
}
_BASE_FIELDS = frozenset(
    {
        "veridian.assurance.schema_id",
        "veridian.assurance.stage",
        "veridian.authorization.digest",
        "veridian.decision.digest",
        "veridian.decision.status",
    }
)
_PERMIT_FIELDS = frozenset(
    {
        "veridian.semantic.digest",
        "veridian.permit.digest",
        "veridian.permit.status",
    }
)
_EXECUTION_FIELDS = frozenset(
    {
        "veridian.execution.digest",
        "veridian.execution.status",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "veridian.receipt.digest",
        "veridian.receipt.status",
    }
)
_DECISION_STATUSES = frozenset({"allow", "deny", "hold"})
_EXECUTION_STATUSES = frozenset(
    {
        "permit_redeemed",
        "dispatched",
        "acknowledged",
        "committed",
        "failed",
        "compensated",
    }
)
_RECEIPT_STATUSES = frozenset(
    {"redeemed", "dispatched", "acknowledged", "committed", "failed", "compensated"}
)


def _required_fields(stage: TelemetryStage) -> frozenset[str]:
    fields = _BASE_FIELDS
    if stage in (TelemetryStage.PERMIT, TelemetryStage.EXECUTION, TelemetryStage.RECEIPT):
        fields |= _PERMIT_FIELDS
    if stage in (TelemetryStage.EXECUTION, TelemetryStage.RECEIPT):
        fields |= _EXECUTION_FIELDS
    if stage is TelemetryStage.RECEIPT:
        fields |= _RECEIPT_FIELDS
    return fields


@dataclass(frozen=True)
class AssuranceTelemetryEventV1:
    """One exact event/attribute mapping for a reached assurance stage."""

    stage: TelemetryStage
    attributes: Mapping[str, TelemetryAttributeValue]

    def __post_init__(self) -> None:
        if not isinstance(self.stage, TelemetryStage):
            raise AssuranceValidationError("stage must be a TelemetryStage")
        if not isinstance(self.attributes, Mapping):
            raise AssuranceValidationError("telemetry attributes must be an object")
        attributes = dict(self.attributes)
        expected = _required_fields(self.stage)
        if attributes.keys() != expected:
            missing = sorted(expected - attributes.keys())
            unknown = sorted(attributes.keys() - expected)
            raise AssuranceValidationError(
                f"invalid telemetry attributes: missing={missing}, unknown={unknown}"
            )
        if attributes["veridian.assurance.schema_id"] != OTEL_SEMCONV_SCHEMA_V1:
            raise AssuranceValidationError("unsupported telemetry semantic-convention schema")
        if attributes["veridian.assurance.stage"] != self.stage.value:
            raise AssuranceValidationError("telemetry stage attribute does not match event stage")
        for key, value in attributes.items():
            if not isinstance(key, str) or not isinstance(value, (bool, int, float, str)):
                raise AssuranceValidationError("telemetry supports scalar attributes only")
            if key.endswith(".digest"):
                require_digest(value, key)
        decision_status = attributes["veridian.decision.status"]
        if decision_status not in _DECISION_STATUSES:
            raise AssuranceValidationError("unsupported decision telemetry status")
        if (
            self.stage
            in (
                TelemetryStage.PERMIT,
                TelemetryStage.EXECUTION,
                TelemetryStage.RECEIPT,
            )
            and attributes["veridian.permit.status"] != "issued"
        ):
            raise AssuranceValidationError("unsupported permit telemetry status")
        if (
            self.stage in (TelemetryStage.EXECUTION, TelemetryStage.RECEIPT)
            and attributes["veridian.execution.status"] not in _EXECUTION_STATUSES
        ):
            raise AssuranceValidationError("unsupported execution telemetry status")
        if (
            self.stage is TelemetryStage.RECEIPT
            and attributes["veridian.receipt.status"] not in _RECEIPT_STATUSES
        ):
            raise AssuranceValidationError("unsupported receipt telemetry status")
        object.__setattr__(self, "attributes", MappingProxyType(attributes))

    @property
    def name(self) -> str:
        """Stable OpenTelemetry event name for this stage."""

        return _EVENT_NAMES[self.stage]


def _identifier_commitment(domain: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssuranceValidationError(f"{domain} must be a non-empty string")
    return sha256_digest(f"{domain}\x00{value}".encode())


@dataclass(frozen=True)
class AssuranceTelemetryLinkV1:
    """Validated, privacy-preserving decision-to-receipt linkage.

    Use the named constructors/binders in lifecycle order.  Raw permit and
    effect IDs are reduced to domain-separated commitments used only for exact
    linkage validation; those commitments are not exported.
    """

    authorization_digest: str
    decision_digest: str
    decision_status: str
    semantic_digest: str | None = None
    permit_digest: str | None = None
    execution_digest: str | None = None
    execution_status: str | None = None
    receipt_digest: str | None = None
    receipt_status: str | None = None
    _permit_id_commitment: str | None = None
    _effect_id_commitment: str | None = None
    _expected_receipt_digest: str | None = None

    def __post_init__(self) -> None:
        require_digest(self.authorization_digest, "authorization_digest")
        require_digest(self.decision_digest, "decision_digest")
        if self.decision_status not in _DECISION_STATUSES:
            raise AssuranceValidationError("unsupported decision telemetry status")
        for field_name in (
            "semantic_digest",
            "permit_digest",
            "execution_digest",
            "receipt_digest",
            "_permit_id_commitment",
            "_effect_id_commitment",
            "_expected_receipt_digest",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_digest(value, field_name)
        if (self.permit_digest is None) != (self.semantic_digest is None):
            raise AssuranceValidationError("permit and semantic telemetry must be bound together")
        if (self.execution_digest is None) != (self.execution_status is None):
            raise AssuranceValidationError("execution digest and status must be bound together")
        if (self.receipt_digest is None) != (self.receipt_status is None):
            raise AssuranceValidationError("receipt digest and status must be bound together")
        if self.execution_digest is not None and self.permit_digest is None:
            raise AssuranceValidationError("execution telemetry requires a permit")
        if self.receipt_digest is not None and self.execution_digest is None:
            raise AssuranceValidationError("receipt telemetry requires an execution observation")
        if self.execution_status is not None and self.execution_status not in _EXECUTION_STATUSES:
            raise AssuranceValidationError("unsupported execution telemetry status")
        if self.receipt_status is not None and self.receipt_status not in _RECEIPT_STATUSES:
            raise AssuranceValidationError("unsupported receipt telemetry status")

    @classmethod
    def from_decision(cls, decision: DecisionPayloadV1) -> AssuranceTelemetryLinkV1:
        """Start a link from one canonical decision."""

        if not isinstance(decision, DecisionPayloadV1):
            raise AssuranceValidationError("decision must be DecisionPayloadV1")
        return cls(
            authorization_digest=decision.authorization_envelope_digest,
            decision_digest=decision.digest,
            decision_status=decision.disposition.value,
        )

    def bind_permit(self, permit: ExecutionPermitV1) -> AssuranceTelemetryLinkV1:
        """Bind the exact single-use permit issued from this decision."""

        if self.permit_digest is not None:
            raise AssuranceValidationError("a permit is already bound")
        if self.decision_status != "allow":
            raise AssuranceValidationError("only an ALLOW decision can bind a permit")
        if permit.authorization_envelope_digest != self.authorization_digest:
            raise AssuranceValidationError("permit authorization link does not match decision")
        if permit.decision_digest != self.decision_digest:
            raise AssuranceValidationError("permit decision link does not match decision")
        return replace(
            self,
            semantic_digest=permit.semantic_digest,
            permit_digest=permit.digest,
            _permit_id_commitment=_identifier_commitment("permit-id", permit.permit_id),
        )

    def observe_execution(self, event: EffectEventV1) -> AssuranceTelemetryLinkV1:
        """Bind one permit-bearing execution event without exporting its raw details."""

        if self.permit_digest is None or self._permit_id_commitment is None:
            raise AssuranceValidationError("execution observation requires a bound permit")
        if self.execution_digest is not None:
            raise AssuranceValidationError("an execution event is already bound")
        if event.authorization_envelope_digest != self.authorization_digest:
            raise AssuranceValidationError("execution authorization link does not match permit")
        if event.semantic_digest != self.semantic_digest:
            raise AssuranceValidationError("execution semantic link does not match permit")
        if event.permit_id is None:
            raise AssuranceValidationError("execution event must bind a permit_id")
        if _identifier_commitment("permit-id", event.permit_id) != self._permit_id_commitment:
            raise AssuranceValidationError("execution permit link does not match permit")
        status = event.event_type.value
        if status not in _EXECUTION_STATUSES:
            raise AssuranceValidationError("event is not a permit-bearing execution observation")
        return replace(
            self,
            execution_digest=event.digest,
            execution_status=status,
            _effect_id_commitment=_identifier_commitment("effect-id", event.effect_id),
            _expected_receipt_digest=event.receipt_digest,
        )

    def bind_receipt(self, receipt: EffectReceiptV1) -> AssuranceTelemetryLinkV1:
        """Bind the exact effect receipt for the observed execution milestone."""

        if self.execution_digest is None or self._effect_id_commitment is None:
            raise AssuranceValidationError("receipt binding requires an execution observation")
        if self.receipt_digest is not None:
            raise AssuranceValidationError("a receipt is already bound")
        if receipt.authorization_envelope_digest != self.authorization_digest:
            raise AssuranceValidationError("receipt authorization link does not match execution")
        if receipt.semantic_digest != self.semantic_digest:
            raise AssuranceValidationError("receipt semantic link does not match execution")
        if receipt.permit_digest != self.permit_digest:
            raise AssuranceValidationError("receipt permit link does not match execution")
        if _identifier_commitment("effect-id", receipt.effect_id) != self._effect_id_commitment:
            raise AssuranceValidationError("receipt effect link does not match execution")
        if (
            self._expected_receipt_digest is not None
            and receipt.digest != self._expected_receipt_digest
        ):
            raise AssuranceValidationError("receipt digest does not match execution event")
        receipt_status = receipt.receipt_type.value
        expected_status = (
            "redeemed" if self.execution_status == "permit_redeemed" else self.execution_status
        )
        if receipt_status != expected_status:
            raise AssuranceValidationError("receipt status does not match execution status")
        return replace(
            self,
            receipt_digest=receipt.digest,
            receipt_status=receipt_status,
        )

    def event(self, stage: TelemetryStage) -> AssuranceTelemetryEventV1:
        """Render the exact v1 attributes for a reached lifecycle stage."""

        if not isinstance(stage, TelemetryStage):
            raise AssuranceValidationError("stage must be a TelemetryStage")
        attributes: dict[str, TelemetryAttributeValue] = {
            "veridian.assurance.schema_id": OTEL_SEMCONV_SCHEMA_V1,
            "veridian.assurance.stage": stage.value,
            "veridian.authorization.digest": self.authorization_digest,
            "veridian.decision.digest": self.decision_digest,
            "veridian.decision.status": self.decision_status,
        }
        if stage in (TelemetryStage.PERMIT, TelemetryStage.EXECUTION, TelemetryStage.RECEIPT):
            if self.semantic_digest is None or self.permit_digest is None:
                raise AssuranceValidationError("permit telemetry stage has not been reached")
            attributes.update(
                {
                    "veridian.semantic.digest": self.semantic_digest,
                    "veridian.permit.digest": self.permit_digest,
                    "veridian.permit.status": "issued",
                }
            )
        if stage in (TelemetryStage.EXECUTION, TelemetryStage.RECEIPT):
            if self.execution_digest is None or self.execution_status is None:
                raise AssuranceValidationError("execution telemetry stage has not been reached")
            attributes.update(
                {
                    "veridian.execution.digest": self.execution_digest,
                    "veridian.execution.status": self.execution_status,
                }
            )
        if stage is TelemetryStage.RECEIPT:
            if self.receipt_digest is None or self.receipt_status is None:
                raise AssuranceValidationError("receipt telemetry stage has not been reached")
            attributes.update(
                {
                    "veridian.receipt.digest": self.receipt_digest,
                    "veridian.receipt.status": self.receipt_status,
                }
            )
        return AssuranceTelemetryEventV1(stage, attributes)


@runtime_checkable
class SpanLike(Protocol):
    """Minimal structural seam implemented by an OpenTelemetry Span or wrapper."""

    def set_attribute(self, key: str, value: TelemetryAttributeValue) -> object:
        """Set one scalar span attribute."""

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, TelemetryAttributeValue] | None = None,
    ) -> object:
        """Add one named span event."""


def export_telemetry_event(span: SpanLike, event: AssuranceTelemetryEventV1) -> None:
    """Export an immutable mapping to a caller-owned Span-like object."""

    if not isinstance(span, SpanLike):
        raise AssuranceValidationError("span must provide set_attribute() and add_event()")
    if not isinstance(event, AssuranceTelemetryEventV1):
        raise AssuranceValidationError("event must be AssuranceTelemetryEventV1")
    try:
        for key, value in event.attributes.items():
            span.set_attribute(key, value)
        span.add_event(event.name, event.attributes)
    except Exception as exc:
        raise AssuranceValidationError("Span-like telemetry export failed") from exc
