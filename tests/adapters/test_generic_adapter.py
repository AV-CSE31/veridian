from __future__ import annotations

from chit.adapters import ActionSpecV1, GenericActionAdapter


def test_generic_callable_envelope_normalizes_declared_action() -> None:
    adapter = GenericActionAdapter(
        {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    )
    envelope = {
        "schema_id": "chit.generic-action.v1",
        "message_id": "generic-12",
        "action": "transfer_funds",
        "arguments": {
            "amount_minor": 125_000,
            "currency": "USD",
            "destination_account": "account:merchant-42",
        },
    }

    normalized = adapter.normalize(envelope)

    assert normalized.semantics.action_type == "bank.transfer"
    assert normalized.semantics.target == "account:merchant-42"
    assert normalized.transport.protocol == "chit.generic-action"
    assert normalized.transport.message_id == "generic-12"
