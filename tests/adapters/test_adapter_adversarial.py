from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from veridian.adapters import (
    ActionSpecV1,
    AdapterValidationError,
    GenericActionAdapter,
    LangGraphToolCallAdapter,
    MCPToolCallAdapter,
    OpenAIResponsesAdapter,
    UnknownActionError,
)
from veridian.core.exceptions import VeridianError

SPECS = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
ARGS = {
    "amount_minor": 125_000,
    "currency": "USD",
    "destination_account": "account:merchant-42",
}


def _openai(arguments: str) -> dict[str, object]:
    return {
        "type": "function_call",
        "call_id": "call-openai",
        "name": "transfer_funds",
        "arguments": arguments,
    }


@pytest.mark.parametrize(
    ("normalize", "message"),
    [
        (
            OpenAIResponsesAdapter(SPECS).normalize,
            _openai(
                '{"amount_minor":125000.0,"currency":"USD",'
                '"destination_account":"account:merchant-42"}'
            ),
        ),
        (
            MCPToolCallAdapter(SPECS, protocol_version="2025-06-18").normalize,
            {
                "jsonrpc": "2.0",
                "id": "mcp-1",
                "method": "tools/call",
                "params": {
                    "name": "transfer_funds",
                    "arguments": {**ARGS, "exchange_rate": 1.25},
                },
            },
        ),
        (
            LangGraphToolCallAdapter(SPECS).normalize,
            {
                "id": "langgraph-1",
                "name": "transfer_funds",
                "args": {**ARGS, "amount_minor": 2**53},
                "type": "tool_call",
            },
        ),
        (
            GenericActionAdapter(SPECS).normalize,
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "generic-1",
                "action": "transfer_funds",
                "arguments": {**ARGS, "memo": "e\u0301"},
            },
        ),
    ],
)
def test_all_adapters_reject_values_outside_canonical_profile(
    normalize: Callable[[object], object], message: object
) -> None:
    with pytest.raises(AdapterValidationError) as caught:
        normalize(message)

    assert isinstance(caught.value, VeridianError)


def test_openai_rejects_noncanonical_and_duplicate_argument_json() -> None:
    adapter = OpenAIResponsesAdapter(SPECS)
    noncanonical = _openai(
        '{"currency": "USD", "amount_minor": 125000, "destination_account": "account:merchant-42"}'
    )
    duplicate = _openai(
        '{"amount_minor":125000,"amount_minor":125001,"currency":"USD",'
        '"destination_account":"account:merchant-42"}'
    )

    with pytest.raises(AdapterValidationError):
        adapter.normalize(noncanonical)
    with pytest.raises(AdapterValidationError):
        adapter.normalize(duplicate)


@pytest.mark.parametrize(
    ("normalize", "message"),
    [
        (
            OpenAIResponsesAdapter(SPECS).normalize,
            {
                **_openai(
                    '{"amount_minor":125000,"currency":"USD",'
                    '"destination_account":"account:merchant-42"}'
                ),
                "method": "tools/call",
            },
        ),
        (
            MCPToolCallAdapter(SPECS, protocol_version="2025-06-18").normalize,
            {
                "jsonrpc": "2.0",
                "id": "mcp-1",
                "method": "tools/call",
                "params": {"name": "transfer_funds", "arguments": ARGS, "tool": "other"},
            },
        ),
        (
            LangGraphToolCallAdapter(SPECS).normalize,
            {
                "id": "langgraph-1",
                "name": "transfer_funds",
                "args": ARGS,
                "type": "tool_call",
                "arguments": ARGS,
            },
        ),
        (
            GenericActionAdapter(SPECS).normalize,
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "generic-1",
                "action": "transfer_funds",
                "name": "other_action",
                "arguments": ARGS,
            },
        ),
    ],
)
def test_unknown_or_ambiguous_wire_shapes_fail_closed(
    normalize: Callable[[object], object], message: object
) -> None:
    with pytest.raises(AdapterValidationError):
        normalize(message)


def test_unregistered_action_and_missing_target_fail_closed() -> None:
    adapter = GenericActionAdapter(SPECS)

    with pytest.raises(UnknownActionError):
        adapter.normalize(
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "generic-1",
                "action": "delete_ledger",
                "arguments": ARGS,
            }
        )
    with pytest.raises(AdapterValidationError):
        adapter.normalize(
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "generic-2",
                "action": "transfer_funds",
                "arguments": {"amount_minor": 125_000, "currency": "USD"},
            }
        )


def test_transport_only_mutation_preserves_semantics_and_changes_transport() -> None:
    adapter = GenericActionAdapter(SPECS)
    base = {
        "schema_id": "veridian.generic-action.v1",
        "message_id": "generic-1",
        "action": "transfer_funds",
        "arguments": ARGS,
    }

    first = adapter.normalize(base)
    second = adapter.normalize({**base, "message_id": "generic-2"})

    assert first.semantics.digest == second.semantics.digest
    assert first.transport.digest != second.transport.digest
    assert first.transport.raw_message_digest != second.transport.raw_message_digest


def test_noncanonical_raw_json_bytes_are_rejected() -> None:
    adapter = GenericActionAdapter(SPECS)
    noncanonical = (
        b'{"schema_id": "veridian.generic-action.v1", "message_id": "generic-1", '
        b'"action": "transfer_funds", "arguments": {"amount_minor": 125000, '
        b'"currency": "USD", "destination_account": "account:merchant-42"}}'
    )

    with pytest.raises(AdapterValidationError):
        adapter.normalize(noncanonical)


@pytest.mark.parametrize(
    "message",
    [
        _openai(
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        )
        | {"status": "in_progress"},
        _openai(
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        )
        | {"id": 42},
        _openai(
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        )
        | {"caller": {"type": "program"}},
        _openai(
            '{"amount_minor":125000,"currency":"USD","destination_account":"account:merchant-42"}'
        )
        | {"caller": {"type": "unknown"}},
    ],
)
def test_openai_incomplete_or_malformed_optional_fields_fail_closed(
    message: dict[str, object],
) -> None:
    with pytest.raises(AdapterValidationError):
        OpenAIResponsesAdapter(SPECS).normalize(message)


@pytest.mark.parametrize("request_id", [True, None, [], {}])
def test_mcp_rejects_ambiguous_json_rpc_ids(request_id: object) -> None:
    message = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "transfer_funds", "arguments": ARGS},
    }

    with pytest.raises(AdapterValidationError):
        MCPToolCallAdapter(SPECS, protocol_version="2026-07-28").normalize(message)


def test_sdk_like_objects_with_hidden_state_are_rejected() -> None:
    message = SimpleNamespace(
        schema_id="veridian.generic-action.v1",
        message_id="generic-1",
        action="transfer_funds",
        arguments=ARGS,
        _hidden_mutation="delete-account",
    )

    with pytest.raises(AdapterValidationError):
        GenericActionAdapter(SPECS).normalize(message)
