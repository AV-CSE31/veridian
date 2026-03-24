"""
veridian.providers.base
───────────────────────
LLMProvider ABC + Message/LLMResponse types.
All LLM I/O flows through these types — swapping providers requires
no changes to the rest of the codebase.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Message:
    role: str        # "system" | "user" | "assistant"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[Any] | None = None


@dataclass
class LLMResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    tool_calls: list[Any] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        ...

    @abstractmethod
    async def complete_async(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        ...

    def count_tokens(self, text: str) -> int:
        """Approximate token count. Override with provider-specific tokeniser."""
        return max(1, len(text) // 4)

    def stream(self, messages: list[Message], **kwargs: Any) -> Generator[str, None, None]:
        """Default: non-streaming. Override for streaming support."""
        yield self.complete(messages, **kwargs).content
