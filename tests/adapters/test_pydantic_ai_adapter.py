from __future__ import annotations

import sys

import pytest

from veridian.adapters import (
    ActionSpecV1,
    AdapterValidationError,
    GenericActionAdapter,
    PydanticAIDeferredToolAdapter,
)

SPECS = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
ARGS = {
    "amount_minor": 125_000,
    "currency": "USD",
    "destination_account": "account:merchant-42",
}


def _request(kind: str = "approval") -> dict[str, object]:
    return {
        "schema_id": "veridian.pydantic-ai.deferred-tool.v1",
        "request_kind": kind,
        "tool_call": {
            "tool_name": "transfer_funds",
            "args": ARGS,
            "tool_call_id": "pyd-ai-call-1",
        },
    }


def test_pydantic_deferred_profile_has_same_business_semantics_as_generic() -> None:
    pydantic = PydanticAIDeferredToolAdapter(SPECS).normalize(_request())
    generic = GenericActionAdapter(SPECS).normalize(
        {
            "schema_id": "veridian.generic-action.v1",
            "message_id": "generic-1",
            "action": "transfer_funds",
            "arguments": ARGS,
        }
    )

    assert pydantic.semantics == generic.semantics
    assert pydantic.transport.protocol == "pydantic-ai.deferred-tool"
    assert pydantic.transport.protocol_version == "v1"
    assert pydantic.transport.message_id == "pyd-ai-call-1"
    assert pydantic.transport != generic.transport


def test_approval_and_external_calls_share_semantics_but_not_transport() -> None:
    adapter = PydanticAIDeferredToolAdapter(SPECS)

    approval = adapter.normalize(_request("approval"))
    external = adapter.normalize(_request("external"))

    assert approval.semantics == external.semantics
    assert approval.transport.raw_message_digest != external.transport.raw_message_digest


def test_adapter_is_dependency_free_and_never_imports_pydantic_ai() -> None:
    before = set(sys.modules)

    PydanticAIDeferredToolAdapter(SPECS).normalize(_request())

    imported = set(sys.modules) - before
    assert not any(name == "pydantic_ai" or name.startswith("pydantic_ai.") for name in imported)


@pytest.mark.parametrize(
    "message",
    [
        _request("unsupported"),
        {**_request(), "metadata": {"customer_name": "Ada"}},
        {
            **_request(),
            "tool_call": {**_request()["tool_call"], "args": '{"amount_minor":125000}'},
        },
        {
            **_request(),
            "tool_call": {**_request()["tool_call"], "tool_call_id": ""},
        },
        {
            "schema_id": "veridian.pydantic-ai.deferred-tool.v1",
            "request_kind": "approval",
            "approvals": [_request()["tool_call"]],
        },
    ],
)
def test_malformed_or_ambiguous_pydantic_profiles_fail_closed(
    message: dict[str, object],
) -> None:
    with pytest.raises(AdapterValidationError):
        PydanticAIDeferredToolAdapter(SPECS).normalize(message)


def test_canonical_profile_bytes_bind_the_exact_transport() -> None:
    adapter = PydanticAIDeferredToolAdapter(SPECS)
    canonical = (
        b'{"request_kind":"approval","schema_id":"veridian.pydantic-ai.deferred-tool.v1",'
        b'"tool_call":{"args":{"amount_minor":125000,"currency":"USD",'
        b'"destination_account":"account:merchant-42"},"tool_call_id":"pyd-ai-call-1",'
        b'"tool_name":"transfer_funds"}}'
    )

    assert adapter.normalize(canonical) == adapter.normalize(_request())
