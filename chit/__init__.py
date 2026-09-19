"""Deterministic verification and replay-safe execution for AI agent tasks."""

from __future__ import annotations

__version__ = "0.4.0"
__author__ = "Chit contributors"
__license__ = "MIT"

_LAZY_EXPORTS = {
    "BaseHook": "chit.hooks.base:BaseHook",
    "BaseVerifier": "chit.verify.base:BaseVerifier",
    "Check": "chit.gate:Check",
    "CheckContext": "chit.gate:CheckContext",
    "CheckOutcome": "chit.gate:CheckOutcome",
    "Gate": "chit.gate:Gate",
    "GateDeniedError": "chit.gate:GateDeniedError",
    "GateHeldError": "chit.gate:GateHeldError",
    "GateOutcome": "chit.gate:GateOutcome",
    "Verdict": "chit.gate:Verdict",
    "check": "chit.gate:check",
    "HookRegistry": "chit.hooks.registry:HookRegistry",
    "LiteLLMProvider": "chit.providers.litellm_provider:LiteLLMProvider",
    "LLMProvider": "chit.providers.base:LLMProvider",
    "LLMResponse": "chit.providers.base:LLMResponse",
    "Message": "chit.providers.base:Message",
    "MockProvider": "chit.providers.mock_provider:MockProvider",
    "ProviderError": "chit.core.exceptions:ProviderError",
    "RunSummary": "chit.loop.runner:RunSummary",
    "Task": "chit.core.task:Task",
    "TaskLedger": "chit.ledger.ledger:TaskLedger",
    "ChitConfig": "chit.core.config:ChitConfig",
    "ChitError": "chit.core.exceptions:ChitError",
    "ChitRunner": "chit.loop.runner:ChitRunner",
    "VerifiedCall": "chit.decorators:VerifiedCall",
    "VerificationError": "chit.core.exceptions:VerificationError",
    "VerificationContract": "chit.core.contract:VerificationContract",
    "VerificationDecision": "chit.core.contract:VerificationDecision",
    "VerificationResult": "chit.verify.base:VerificationResult",
    "VerifierStep": "chit.core.contract:VerifierStep",
    "verify_completion": "chit.core.contract:verify_completion",
    "verified": "chit.decorators:verified",
}


def __getattr__(name: str) -> object:
    """Lazy-load public primitives on first access."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'chit' has no attribute {name!r}")
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
    "ChitRunner",
    "ChitConfig",
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
    "VerificationContract",
    "VerificationDecision",
    "verified",
    "VerifierStep",
    "verify_completion",
    "ChitError",
    "VerificationError",
    "ProviderError",
    # Gate porcelain — the documented front door over the assurance kernel and
    # the effects boundary. Everything it emits is a plain assurance/effects
    # value; see chit.gate for the full surface.
    "Gate",
    "Check",
    "CheckContext",
    "CheckOutcome",
    "Verdict",
    "GateOutcome",
    "GateDeniedError",
    "GateHeldError",
    "check",
]
