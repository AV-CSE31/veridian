"""Provider interfaces and built-in provider implementations.

``LiteLLMProvider`` and ``CircuitBreaker`` are lazy-loaded on first attribute
access --- the underlying module imports ``tenacity`` at module load time
(~25-30ms) and pulling that on every ``import veridian`` slows down the
common configuration-only import path for no benefit.
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
