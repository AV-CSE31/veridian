"""Deterministic verification and replay-safe execution for AI agent tasks."""

from __future__ import annotations

__version__ = "0.4.0"
__author__ = "Veridian contributors"
__license__ = "MIT"

_LAZY_EXPORTS = {
    "BaseHook": "veridian.hooks.base:BaseHook",
    "BaseVerifier": "veridian.verify.base:BaseVerifier",
    "HookRegistry": "veridian.hooks.registry:HookRegistry",
    "LiteLLMProvider": "veridian.providers.litellm_provider:LiteLLMProvider",
    "LLMProvider": "veridian.providers.base:LLMProvider",
    "LLMResponse": "veridian.providers.base:LLMResponse",
    "Message": "veridian.providers.base:Message",
    "MockProvider": "veridian.providers.mock_provider:MockProvider",
    "ProviderError": "veridian.core.exceptions:ProviderError",
    "RunSummary": "veridian.loop.runner:RunSummary",
    "Task": "veridian.core.task:Task",
    "TaskLedger": "veridian.ledger.ledger:TaskLedger",
    "VeridianConfig": "veridian.core.config:VeridianConfig",
    "VeridianError": "veridian.core.exceptions:VeridianError",
    "VeridianRunner": "veridian.loop.runner:VeridianRunner",
    "VerifiedCall": "veridian.decorators:VerifiedCall",
    "VerificationError": "veridian.core.exceptions:VerificationError",
    "VerificationResult": "veridian.verify.base:VerificationResult",
    "verified": "veridian.decorators:verified",
}


def __getattr__(name: str) -> object:
    """Lazy-load public primitives on first access."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'veridian' has no attribute {name!r}")
    module_name, attr_name = target.rsplit(":", 1)
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "__version__",
    "Task",
    "TaskLedger",
    "VeridianRunner",
    "VeridianConfig",
    "RunSummary",
    "BaseVerifier",
    "VerificationResult",
    "BaseHook",
    "HookRegistry",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "MockProvider",
    "LiteLLMProvider",
    "VerifiedCall",
    "verified",
    "VeridianError",
    "VerificationError",
    "ProviderError",
]
