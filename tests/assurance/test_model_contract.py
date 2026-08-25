from __future__ import annotations

from datetime import UTC, datetime

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AssuranceValidationError,
    AuthorizationEnvelope,
    CompletionSemanticsV1,
    TransportBinding,
)


def test_action_semantics_has_an_independent_golden_encoding_and_digest() -> None:
    action = ActionSemanticsV1(
        action_type="bank.transfer",
        target="account:merchant-42",
        parameters={"currency": "USD", "amount_minor": 125_000},
    )

    assert action.to_bytes() == (
        b'{"action_type":"bank.transfer","parameters":{"amount_minor":125000,'
        b'"currency":"USD"},"schema_id":"veridian.action-semantics.v1",'
        b'"target":"account:merchant-42"}'
    )
    assert (
        action.digest == "sha256:47ef12a7aa729ad017685ee0d82c1ac7b00cc39eebba62ba2996affe54986d65"
    )
    assert ActionSemanticsV1.from_bytes(action.to_bytes()) == action


def test_completion_semantics_is_protocol_neutral() -> None:
    completion = CompletionSemanticsV1(
        completion_type="payment.reconciled",
        subject="payment:PAY-9001",
        assertions={"currency": "EUR", "settled_minor": 88_050},
    )

    encoded = completion.to_bytes()

    assert b"mcp" not in encoded
    assert b"openai" not in encoded
    assert CompletionSemanticsV1.from_bytes(encoded) == completion


def test_authorization_and_transport_are_separate_exact_bindings() -> None:
    action = ActionSemanticsV1(
        action_type="bank.transfer",
        target="account:merchant-42",
        parameters={"amount_minor": 125_000, "currency": "USD"},
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="nonce-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest="sha256:" + "a" * 64,
        policy_digest="sha256:" + "b" * 64,
    )
    transport = TransportBinding(
        adapter_id="mcp-gateway",
        adapter_version="2.1.0",
        protocol="mcp",
        protocol_version="2026-07-28",
        message_id="req-991",
        raw_message_digest="sha256:" + "c" * 64,
    )

    assert action.digest in authorization.to_bytes().decode("utf-8")
    assert b"mcp" not in authorization.to_bytes()
    assert b"mcp" in transport.to_bytes()
    assert AuthorizationEnvelope.from_bytes(authorization.to_bytes()) == authorization
    assert TransportBinding.from_bytes(transport.to_bytes()) == transport


@pytest.mark.parametrize(
    "payload",
    [
        {"float": 1.25},
        {"non_nfc": "e\u0301"},
        {"too_large": 2**53},
    ],
)
def test_canonical_profile_rejects_ambiguous_values(payload: dict[str, object]) -> None:
    with pytest.raises(AssuranceValidationError):
        ActionSemanticsV1("test", "target", payload)


def test_authorization_rejects_non_utc_or_reversed_validity_window() -> None:
    with pytest.raises(AssuranceValidationError):
        AuthorizationEnvelope(
            semantic_kind="action",
            semantic_digest="sha256:" + "1" * 64,
            principal_id="agent:test",
            delegation_chain=(),
            audience="executor:test",
            purpose="test",
            nonce="nonce-0123456789abcdef",
            not_before="2026-08-19T10:05:00Z",
            expires_at="2026-08-19T10:00:00Z",
            state_digest="sha256:" + "2" * 64,
            policy_digest="sha256:" + "3" * 64,
        )

    # The public representation is second-precision UTC, not locale-dependent.
    assert datetime.fromisoformat("2026-08-19T10:00:00+00:00").tzinfo is UTC
