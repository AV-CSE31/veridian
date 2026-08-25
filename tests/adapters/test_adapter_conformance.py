from __future__ import annotations

from veridian.adapters import (
    ActionAdapter,
    ActionSpecV1,
    GenericActionAdapter,
    LangGraphToolCallAdapter,
    MCPToolCallAdapter,
    OpenAIResponsesAdapter,
)

EXPECTED_SEMANTIC_DIGEST = "sha256:ea1f897fb86084a58b535e3a6a3c2c2810b778ae277dcb07cada86db0568920f"


def test_protocol_adapters_share_one_golden_semantic_identity() -> None:
    specs = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    arguments = {
        "amount_minor": 125_000,
        "currency": "USD",
        "destination_account": "account:merchant-42",
    }
    normalized = [
        OpenAIResponsesAdapter(specs).normalize(
            {
                "type": "function_call",
                "call_id": "call-openai",
                "name": "transfer_funds",
                "arguments": (
                    '{"amount_minor":125000,"currency":"USD",'
                    '"destination_account":"account:merchant-42"}'
                ),
            }
        ),
        MCPToolCallAdapter(specs, protocol_version="2025-06-18").normalize(
            {
                "jsonrpc": "2.0",
                "id": "call-mcp",
                "method": "tools/call",
                "params": {"name": "transfer_funds", "arguments": arguments},
            }
        ),
        LangGraphToolCallAdapter(specs).normalize(
            {
                "id": "call-langgraph",
                "name": "transfer_funds",
                "args": arguments,
                "type": "tool_call",
            }
        ),
        GenericActionAdapter(specs).normalize(
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "call-generic",
                "action": "transfer_funds",
                "arguments": arguments,
            }
        ),
    ]

    assert {item.semantics.digest for item in normalized} == {EXPECTED_SEMANTIC_DIGEST}
    assert len({item.transport.digest for item in normalized}) == 4
    assert len({item.transport.protocol for item in normalized}) == 4


def test_concrete_adapters_share_one_framework_neutral_interface() -> None:
    specs = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}

    adapters = (
        OpenAIResponsesAdapter(specs),
        MCPToolCallAdapter(specs, protocol_version="2025-06-18"),
        LangGraphToolCallAdapter(specs),
        GenericActionAdapter(specs),
    )

    assert all(isinstance(adapter, ActionAdapter) for adapter in adapters)


def test_business_argument_mutation_changes_semantics_but_not_adapter_identity() -> None:
    specs = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    adapter = GenericActionAdapter(specs)
    base = {
        "schema_id": "veridian.generic-action.v1",
        "message_id": "call-generic",
        "action": "transfer_funds",
        "arguments": {
            "amount_minor": 125_000,
            "currency": "USD",
            "destination_account": "account:merchant-42",
        },
    }
    mutated = {
        **base,
        "arguments": {**base["arguments"], "amount_minor": 1_250_000},
    }

    original = adapter.normalize(base)
    changed = adapter.normalize(mutated)

    assert original.semantics.digest != changed.semantics.digest
    assert original.transport.adapter_id == changed.transport.adapter_id
    assert original.transport.raw_message_digest != changed.transport.raw_message_digest


def test_canonical_bytes_and_mapping_bind_the_same_exact_transport_record() -> None:
    specs = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    adapter = GenericActionAdapter(specs)
    mapping = {
        "schema_id": "veridian.generic-action.v1",
        "message_id": "generic-1",
        "action": "transfer_funds",
        "arguments": {
            "amount_minor": 125_000,
            "currency": "USD",
            "destination_account": "account:merchant-42",
        },
    }
    canonical_bytes = (
        b'{"action":"transfer_funds","arguments":{"amount_minor":125000,'
        b'"currency":"USD","destination_account":"account:merchant-42"},'
        b'"message_id":"generic-1","schema_id":"veridian.generic-action.v1"}'
    )

    from_mapping = adapter.normalize(mapping)
    from_bytes = adapter.normalize(canonical_bytes)

    assert from_mapping == from_bytes
