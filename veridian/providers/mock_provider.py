"""
veridian.providers.mock_provider
────────────────────────────────
Deterministic mock for tests. Zero network calls. No API keys.

Usage::

    mock = MockProvider()
    # Script exact responses
    mock.script([
        LLMResponse(content='<veridian:result>{"summary":"done","structured":{"field":"val"}}</veridian:result>'),
        LLMResponse(content='second response'),
    ])

    # Or use a callable
    mock.respond_with(lambda messages: LLMResponse(content="always this"))
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from veridian.providers.base import LLMProvider, LLMResponse, Message


class MockProvider(LLMProvider):
    """
    Deterministic mock LLM provider for unit and integration tests.
    Raises if responses are exhausted before all calls are made.
    """

    def __init__(self, default_tokens: int = 500) -> None:
        self._queue: list[LLMResponse] = []
        self._callable: Callable[[list[Message]], LLMResponse] | None = None
        self._calls: list[list[Message]] = []
        self.default_tokens = default_tokens

    # ── Configuration API ─────────────────────────────────────────────────────

    def script(self, responses: list[LLMResponse]) -> MockProvider:
        """Queue responses to be returned in order."""
        self._queue.extend(responses)
        return self

    def script_text(self, *texts: str) -> MockProvider:
        """Convenience: queue plain text responses."""
        for t in texts:
            self._queue.append(
                LLMResponse(
                    content=t,
                    input_tokens=self.default_tokens,
                    output_tokens=len(t) // 4,
                    model="mock",
                )
            )
        return self

    def respond_with(self, fn: Callable[[list[Message]], LLMResponse]) -> MockProvider:
        """Use a callable to generate responses."""
        self._callable = fn
        return self

    def script_veridian_result(
        self,
        structured: dict[str, Any],
        summary: str = "done",
    ) -> MockProvider:
        """Convenience: script a valid veridian:result block."""
        import json

        payload = json.dumps({"summary": summary, "structured": structured, "artifacts": []})
        text = f"<veridian:result>\n{payload}\n</veridian:result>"
        return self.script_text(text)

    # ── LLMProvider interface ─────────────────────────────────────────────────

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self._calls.append(messages)

        # Callable takes priority
        if self._callable:
            return self._callable(messages)

        # Queue
        if self._queue:
            return self._queue.pop(0)

        # Default fallback
        return LLMResponse(
            content=(
                "<veridian:result>\n"
                '{"summary": "mock done", "structured": {}, "artifacts": []}\n'
                "</veridian:result>"
            ),
            input_tokens=self.default_tokens,
            output_tokens=50,
            model="mock",
        )

    async def complete_async(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        return self.complete(messages, **kwargs)

    # ── Inspection helpers ────────────────────────────────────────────────────

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def last_messages(self) -> list[Message]:
        return self._calls[-1] if self._calls else []

    def reset(self) -> None:
        self._queue.clear()
        self._callable = None
        self._calls.clear()
