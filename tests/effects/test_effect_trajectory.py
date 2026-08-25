from __future__ import annotations

import pytest

from veridian.effects import (
    EffectEventType,
    EffectEventV1,
    EffectStatus,
    EffectValidationError,
    reduce_effects,
)

_SEMANTIC_DIGEST = "sha256:" + "1" * 64
_AUTHORIZATION_DIGEST = "sha256:" + "2" * 64
_RECEIPT_DIGEST = "sha256:" + "3" * 64


def _event(
    sequence: int,
    event_type: EffectEventType,
    *,
    event_id: str | None = None,
    authorization_digest: str | None = _AUTHORIZATION_DIGEST,
    permit_id: str | None = None,
    receipt_digest: str | None = None,
) -> EffectEventV1:
    return EffectEventV1(
        event_id=event_id or f"evt_{sequence:016d}",
        effect_id="eff_payment_9001",
        sequence=sequence,
        event_type=event_type,
        occurred_at=f"2026-08-19T10:00:{sequence:02d}Z",
        actor_id="service:veridian-executor",
        semantic_digest=_SEMANTIC_DIGEST,
        authorization_envelope_digest=authorization_digest,
        permit_id=permit_id,
        receipt_digest=receipt_digest,
        details={"rail": "rtgs"},
    )


def test_reducer_tracks_an_authorized_payment_to_committed_effect() -> None:
    events = (
        _event(0, EffectEventType.PROPOSED, authorization_digest=None),
        _event(1, EffectEventType.AUTHORIZED),
        _event(2, EffectEventType.PERMIT_ISSUED, permit_id="permit_9001"),
        _event(3, EffectEventType.PERMIT_REDEEMED, permit_id="permit_9001"),
        _event(4, EffectEventType.DISPATCHED, permit_id="permit_9001"),
        _event(
            5,
            EffectEventType.ACKNOWLEDGED,
            permit_id="permit_9001",
            receipt_digest=_RECEIPT_DIGEST,
        ),
        _event(
            6,
            EffectEventType.COMMITTED,
            permit_id="permit_9001",
            receipt_digest=_RECEIPT_DIGEST,
        ),
    )

    state = reduce_effects(events)

    assert state.status is EffectStatus.COMMITTED
    assert state.event_count == 7
    assert state.last_sequence == 6
    assert state.permit_id == "permit_9001"
    assert state.authorization_envelope_digest == _AUTHORIZATION_DIGEST
    assert state.terminal is True
    assert state.head_digest.startswith("sha256:")


def test_reducer_rejects_dispatch_without_atomic_permit_redemption() -> None:
    events = (
        _event(0, EffectEventType.PROPOSED, authorization_digest=None),
        _event(1, EffectEventType.AUTHORIZED),
        _event(2, EffectEventType.PERMIT_ISSUED, permit_id="permit_9001"),
        _event(3, EffectEventType.DISPATCHED, permit_id="permit_9001"),
    )

    with pytest.raises(EffectValidationError, match="PERMIT_REDEEMED"):
        reduce_effects(events)


def test_exact_duplicate_event_is_idempotent_but_conflicting_event_id_is_rejected() -> None:
    proposed = _event(0, EffectEventType.PROPOSED, authorization_digest=None)
    duplicate_state = reduce_effects((proposed, proposed))

    assert duplicate_state.event_count == 1

    conflict = _event(
        1,
        EffectEventType.AUTHORIZED,
        event_id=proposed.event_id,
    )
    with pytest.raises(EffectValidationError, match="conflicting event_id"):
        reduce_effects((proposed, conflict))


def test_sequence_gaps_and_cross_effect_events_are_rejected() -> None:
    proposed = _event(0, EffectEventType.PROPOSED, authorization_digest=None)
    gap = _event(2, EffectEventType.AUTHORIZED)

    with pytest.raises(EffectValidationError, match="sequence"):
        reduce_effects((proposed, gap))

    foreign = EffectEventV1(
        event_id="evt_foreign_00000001",
        effect_id="eff_other",
        sequence=1,
        event_type=EffectEventType.AUTHORIZED,
        occurred_at="2026-08-19T10:00:01Z",
        actor_id="service:veridian-executor",
        semantic_digest=_SEMANTIC_DIGEST,
        authorization_envelope_digest=_AUTHORIZATION_DIGEST,
        permit_id=None,
        receipt_digest=None,
        details={},
    )

    with pytest.raises(EffectValidationError, match="effect_id"):
        reduce_effects((proposed, foreign))


def test_pre_authorization_failure_is_explicit_without_inventing_a_permit() -> None:
    proposed = _event(0, EffectEventType.PROPOSED, authorization_digest=None)
    failed = _event(
        1,
        EffectEventType.FAILED,
        authorization_digest=None,
        permit_id=None,
    )

    state = reduce_effects((proposed, failed))

    assert state.status is EffectStatus.FAILED
    assert state.authorization_envelope_digest is None
    assert state.permit_id is None
    assert state.terminal


def test_authorization_binding_cannot_disappear_after_it_is_established() -> None:
    proposed = _event(0, EffectEventType.PROPOSED, authorization_digest=None)
    authorized = _event(1, EffectEventType.AUTHORIZED)
    failed_without_binding = _event(
        2,
        EffectEventType.FAILED,
        authorization_digest=None,
        permit_id=None,
    )

    with pytest.raises(EffectValidationError, match="authorization envelope"):
        reduce_effects((proposed, authorized, failed_without_binding))
