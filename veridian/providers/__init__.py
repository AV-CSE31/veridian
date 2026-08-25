"""Provider interfaces and built-in provider implementations.

``LiteLLMProvider`` and ``CircuitBreaker`` are lazy-loaded on first attribute
access so the optional provider stack does not slow down the common
configuration-only import path.
"""

from __future__ import annotations

from typing import Any

from veridian.providers.base import LLMProvider, LLMResponse, Message
from veridian.providers.mock_provider import MockProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "Message",
    "CircuitBreaker",
    "LiteLLMProvider",
    "MockProvider",
]


def __getattr__(name: str) -> Any:
    if name in ("LiteLLMProvider", "CircuitBreaker"):
        from veridian.providers.litellm_provider import (  # noqa: PLC0415
            CircuitBreaker,
            LiteLLMProvider,
        )

        globals()["LiteLLMProvider"] = LiteLLMProvider
        globals()["CircuitBreaker"] = CircuitBreaker
        return globals()[name]
    raise AttributeError(f"module 'veridian.providers' has no attribute {name!r}")
