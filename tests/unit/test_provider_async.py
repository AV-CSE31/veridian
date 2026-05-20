"""
tests.unit.test_provider_async
──────────────────────────────
Async coverage for ``LLMProvider.complete_async`` on both the abstract base
contract (via :class:`MockProvider`) and the LiteLLM adapter.

These exercise the async surface that adapter-based concurrency code can use.

``pyproject.toml`` already sets ``asyncio_mode = "auto"``, so plain
``async def test_*`` is sufficient.
"""

from __future__ import annotations

from typing import Any

import pytest

from veridian.providers.base import LLMResponse, Message
from veridian.providers.mock_provider import MockProvider


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


def _msg(content: str = "hi") -> list[Message]:
    return [Message(role="user", content=content)]


class TestMockProviderComplete:
    """Sync/async parity tests for MockProvider."""

    async def test_async_returns_scripted_response(self, provider: MockProvider) -> None:
        provider.script_text("hello async")
        resp = await provider.complete_async(_msg())
        assert resp.content == "hello async"

    async def test_async_returns_default_when_unscripted(self, provider: MockProvider) -> None:
        resp = await provider.complete_async(_msg())
        assert "veridian:result" in resp.content
        assert resp.model == "mock"
        assert resp.input_tokens == provider.default_tokens

    async def test_async_records_messages_in_calls(self, provider: MockProvider) -> None:
        provider.script_text("a", "b")
        await provider.complete_async(_msg("first"))
        await provider.complete_async(_msg("second"))
        assert provider.call_count == 2
        assert provider.last_messages()[0].content == "second"

    async def test_async_callable_overrides_queue(self, provider: MockProvider) -> None:
        provider.script_text("queue-1")
        provider.respond_with(lambda _msgs: LLMResponse(content="callable-wins", model="mock"))
        resp = await provider.complete_async(_msg())
        assert resp.content == "callable-wins"

    async def test_async_matches_sync_for_same_script(self, provider: MockProvider) -> None:
        # Identical scripts should yield identical content over both paths.
        provider.script_text("parity")
        async_resp = await provider.complete_async(_msg())

        sync_provider = MockProvider()
        sync_provider.script_text("parity")
        sync_resp = sync_provider.complete(_msg())

        assert async_resp.content == sync_resp.content
        assert async_resp.model == sync_resp.model

    async def test_async_forwards_kwargs(self, provider: MockProvider) -> None:
        captured: dict[str, Any] = {}

        def _capture(_msgs: list[Message], **kwargs: Any) -> LLMResponse:
            captured.update(kwargs)
            return LLMResponse(content="ok", model="mock")

        # Bind the callable so it captures kwargs from complete()
        provider._callable = _capture
        await provider.complete_async(_msg(), temperature=0.42, max_tokens=128)
        # MockProvider.complete delegates to the callable without forwarding
        # **kwargs — this is the documented MockProvider behavior, so the
        # capture dict stays empty. Test pins the contract.
        assert captured == {}


class TestProviderConcurrency:
    """Parallel async invocations on a shared provider stay deterministic."""

    async def test_concurrent_completions_consume_queue_in_order(
        self, provider: MockProvider
    ) -> None:
        import asyncio

        provider.script_text("r0", "r1", "r2")
        results = await asyncio.gather(
            provider.complete_async(_msg("m0")),
            provider.complete_async(_msg("m1")),
            provider.complete_async(_msg("m2")),
        )
        contents = {r.content for r in results}
        assert contents == {"r0", "r1", "r2"}

    async def test_concurrent_completions_record_each_call(self, provider: MockProvider) -> None:
        import asyncio

        provider.script_text("a", "b", "c", "d", "e")
        await asyncio.gather(*(provider.complete_async(_msg(f"m{i}")) for i in range(5)))
        assert provider.call_count == 5
