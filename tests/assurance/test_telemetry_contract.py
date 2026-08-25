from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AssuranceTelemetryLinkV1,
    AssuranceValidationError,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    TelemetryAttributeValue,
    TelemetryStage,
    export_telemetry_event,
)
from veridian.effects import (
    EffectEventType,
    EffectEventV1,
    EffectReceiptType,
    EffectReceiptV1,
    ExecutionPermitV1,
)

_STATE = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_CONTRACT = "sha256:" + "c" * 64
_MANIFEST = "sha256:" + "7" * 64


def _chain() -> tuple[
    DecisionPayloadV1,
    ExecutionPermitV1,
    EffectEventV1,
    EffectReceiptV1,
]:
    action = ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-secret",
        {
            "amount_minor": 12_500_000,
            "currency": "USD",
            "customer_name": "Ada Secret",
        },
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-secret",
        delegation_chain=("human:alice-secret",),
        audience="bank-executor:prod",
        purpose="invoice:private-314",
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
        evidence_ids=("ev_private",),
        details={"customer_name": "Ada Secret"},
    )
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=_CONTRACT,
        snapshot_digest=_STATE,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )
    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=decision,
        permit_id="permit_private_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-private-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )
    receipt = EffectReceiptV1(
        receipt_id="receipt-private-0123456789abcdef",
        receipt_type=EffectReceiptType.COMMITTED,
        effect_id="effect-private-PAY-9001",
        semantic_digest=permit.semantic_digest,
        authorization_envelope_digest=permit.authorization_envelope_digest,
        permit_digest=permit.digest,
        outbox_id="outbox-private-0123456789abcdef",
        producer_id="bank-simulator:private",
        observed_at="2026-08-19T10:00:14Z",
        external_reference_digest="sha256:" + "4" * 64,
        result_digest="sha256:" + "6" * 64,
        previous_receipt_digest=None,
    )
    event = EffectEventV1(
        event_id="event-private-0123456789abcdef",
        effect_id=receipt.effect_id,
        sequence=6,
        event_type=EffectEventType.COMMITTED,
        occurred_at="2026-08-19T10:00:14Z",
        actor_id="bank-simulator:private",
        semantic_digest=permit.semantic_digest,
        authorization_envelope_digest=permit.authorization_envelope_digest,
        permit_id=permit.permit_id,
        receipt_digest=receipt.digest,
        details={"customer_name": "Ada Secret", "account": "account:merchant-secret"},
    )
    return decision, permit, event, receipt


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, TelemetryAttributeValue] = {}
        self.events: list[tuple[str, dict[str, TelemetryAttributeValue]]] = []

    def set_attribute(self, key: str, value: TelemetryAttributeValue) -> FakeSpan:
        self.attributes[key] = value
        return self

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, TelemetryAttributeValue] | None = None,
    ) -> FakeSpan:
        self.events.append((name, dict(attributes or {})))
        return self


def test_versioned_mapping_propagates_exact_decision_permit_execution_receipt_links() -> None:
    decision, permit, execution, receipt = _chain()
    link = (
        AssuranceTelemetryLinkV1.from_decision(decision)
        .bind_permit(permit)
        .observe_execution(execution)
        .bind_receipt(receipt)
    )

    assert link.event(TelemetryStage.DECISION).attributes == {
        "veridian.assurance.schema_id": "veridian.otel-semconv.v1",
        "veridian.assurance.stage": "decision",
        "veridian.authorization.digest": decision.authorization_envelope_digest,
        "veridian.decision.digest": decision.digest,
        "veridian.decision.status": "allow",
    }
    assert link.event(TelemetryStage.RECEIPT).attributes == {
        "veridian.assurance.schema_id": "veridian.otel-semconv.v1",
        "veridian.assurance.stage": "receipt",
        "veridian.authorization.digest": decision.authorization_envelope_digest,
        "veridian.decision.digest": decision.digest,
        "veridian.decision.status": "allow",
        "veridian.semantic.digest": permit.semantic_digest,
        "veridian.permit.digest": permit.digest,
        "veridian.permit.status": "issued",
        "veridian.execution.digest": execution.digest,
        "veridian.execution.status": "committed",
        "veridian.receipt.digest": receipt.digest,
        "veridian.receipt.status": "committed",
    }


def test_fake_span_export_sets_bounded_attributes_and_adds_one_named_event() -> None:
    decision, permit, execution, receipt = _chain()
    telemetry = (
        AssuranceTelemetryLinkV1.from_decision(decision)
        .bind_permit(permit)
        .observe_execution(execution)
        .bind_receipt(receipt)
        .event(TelemetryStage.RECEIPT)
    )
    span = FakeSpan()

    export_telemetry_event(span, telemetry)

    assert span.attributes == telemetry.attributes
    assert span.events == [("veridian.assurance.receipt", dict(telemetry.attributes))]


def test_telemetry_never_exports_raw_payloads_or_high_cardinality_business_ids() -> None:
    decision, permit, execution, receipt = _chain()
    event = (
        AssuranceTelemetryLinkV1.from_decision(decision)
        .bind_permit(permit)
        .observe_execution(execution)
        .bind_receipt(receipt)
        .event(TelemetryStage.RECEIPT)
    )
    exported = repr(dict(event.attributes))

    for secret in (
        "Ada Secret",
        "merchant-secret",
        "treasury-secret",
        "alice-secret",
        "private-314",
        permit.permit_id,
        permit.idempotency_key,
        execution.event_id,
        execution.effect_id,
        execution.actor_id,
        receipt.receipt_id,
        receipt.outbox_id,
        receipt.producer_id,
    ):
        assert secret not in exported
    assert all(isinstance(value, (bool, int, float, str)) for value in event.attributes.values())


def test_link_substitution_and_out_of_order_binding_fail_closed() -> None:
    decision, permit, execution, receipt = _chain()
    link = AssuranceTelemetryLinkV1.from_decision(decision)

    with pytest.raises(AssuranceValidationError):
        link.observe_execution(execution)
    with pytest.raises(AssuranceValidationError):
        link.bind_receipt(receipt)
    with pytest.raises(AssuranceValidationError):
        link.bind_permit(replace(permit, decision_digest="sha256:" + "0" * 64))

    permitted = link.bind_permit(permit)
    with pytest.raises(AssuranceValidationError):
        permitted.observe_execution(replace(execution, semantic_digest="sha256:" + "1" * 64))
    observed = permitted.observe_execution(execution)
    with pytest.raises(AssuranceValidationError):
        observed.bind_receipt(replace(receipt, effect_id="another-effect"))


def test_unreached_stage_and_receipt_status_mismatch_fail_closed() -> None:
    decision, permit, execution, receipt = _chain()
    link = AssuranceTelemetryLinkV1.from_decision(decision).bind_permit(permit)

    with pytest.raises(AssuranceValidationError):
        link.event(TelemetryStage.EXECUTION)
    with pytest.raises(AssuranceValidationError):
        link.observe_execution(execution).bind_receipt(
            replace(receipt, receipt_type=EffectReceiptType.FAILED)
        )
