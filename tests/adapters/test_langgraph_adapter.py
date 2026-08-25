from __future__ import annotations

from types import SimpleNamespace

from veridian.adapters import ActionSpecV1, LangGraphToolCallAdapter


def test_langgraph_style_object_record_is_accepted_without_sdk_dependency() -> None:
    adapter = LangGraphToolCallAdapter(
        {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    )
    record = SimpleNamespace(
        id="toolu-44",
        name="transfer_funds",
        args={
            "amount_minor": 125_000,
            "currency": "USD",
            "destination_account": "account:merchant-42",
        },
        type="tool_call",
    )

    normalized = adapter.normalize(record)

    assert normalized.semantics.action_type == "bank.transfer"
    assert normalized.semantics.target == "account:merchant-42"
    assert normalized.transport.protocol == "langgraph.tool-call"
    assert normalized.transport.message_id == "toolu-44"
