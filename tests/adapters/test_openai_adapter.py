from __future__ import annotations

from types import SimpleNamespace

from veridian.adapters import ActionSpecV1, OpenAIResponsesAdapter


def test_openai_function_call_normalizes_business_action_and_transport_separately() -> None:
    adapter = OpenAIResponsesAdapter(
        {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    )
    call = {
        "type": "function_call",
        "call_id": "call-9001",
        "name": "transfer_funds",
        "arguments": (
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        ),
    }

    normalized = adapter.normalize(call)

    assert normalized.semantics.action_type == "bank.transfer"
    assert normalized.semantics.target == "account:merchant-42"
    assert normalized.semantics.parameters == {
        "amount_minor": 125_000,
        "currency": "USD",
        "destination_account": "account:merchant-42",
    }
    assert normalized.transport.protocol == "openai.responses"
    assert normalized.transport.message_id == "call-9001"
    assert b"call-9001" not in normalized.semantics.to_bytes()
    assert b"bank.transfer" not in normalized.transport.to_bytes()


def test_openai_current_sdk_object_preserves_known_optional_provenance() -> None:
    adapter = OpenAIResponsesAdapter(
        {"treasury.transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    )
    call = SimpleNamespace(
        arguments=(
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        ),
        call_id="call-9002",
        name="transfer_funds",
        type="function_call",
        id="fc-9002",
        caller=SimpleNamespace(type="direct"),
        namespace="treasury",
        status="completed",
    )

    normalized = adapter.normalize(call)

    assert normalized.semantics.action_type == "bank.transfer"
    assert normalized.transport.message_id == "call-9002"
