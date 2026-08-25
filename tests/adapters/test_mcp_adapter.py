from __future__ import annotations

from veridian.adapters import ActionSpecV1, MCPToolCallAdapter


def test_mcp_json_rpc_tool_call_normalizes_without_executing() -> None:
    adapter = MCPToolCallAdapter(
        {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")},
        protocol_version="2025-06-18",
    )
    request = {
        "jsonrpc": "2.0",
        "id": "request-77",
        "method": "tools/call",
        "params": {
            "name": "transfer_funds",
            "arguments": {
                "amount_minor": 125_000,
                "currency": "USD",
                "destination_account": "account:merchant-42",
            },
        },
    }

    normalized = adapter.normalize(request)

    assert normalized.semantics.action_type == "bank.transfer"
    assert normalized.semantics.target == "account:merchant-42"
    assert normalized.transport.protocol == "mcp.json-rpc"
    assert normalized.transport.protocol_version == "2025-06-18"
    assert normalized.transport.message_id == "request-77"


def test_mcp_current_metadata_and_numeric_json_rpc_id_remain_transport_only() -> None:
    adapter = MCPToolCallAdapter(
        {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")},
        protocol_version="2026-07-28",
    )
    request = {
        "jsonrpc": "2.0",
        "id": 77,
        "method": "tools/call",
        "params": {
            "name": "transfer_funds",
            "arguments": {
                "amount_minor": 125_000,
                "currency": "USD",
                "destination_account": "account:merchant-42",
            },
            "_meta": {
                "io.modelcontextprotocol/clientInfo": {
                    "name": "treasury-agent",
                    "version": "4.2.0",
                }
            },
        },
    }

    normalized = adapter.normalize(request)

    assert normalized.transport.message_id == "jsonrpc-number:77"
    assert b"treasury-agent" not in normalized.semantics.to_bytes()
    assert normalized.transport.protocol_version == "2026-07-28"
