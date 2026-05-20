"""
veridian
--------
Deterministic verification and replay-safe execution for AI agent tasks.

The core contract: a task is not marked DONE unless its verifier passes.

Quick start::

    from veridian import Task, TaskLedger, VeridianRunner, MockProvider

    ledger = TaskLedger("ledger.json")
    ledger.add([
        Task(
            title="Check output schema",
            description="Return JSON with keys: decision, reason.",
            verifier_id="schema",
            verifier_config={"required_fields": ["decision", "reason"]},
        )
    ])

    provider = MockProvider().script_veridian_result(
        structured={"decision": "allow", "reason": "policy-pass"}
    )
    summary = VeridianRunner(ledger=ledger, provider=provider).run()
    print(f"Done: {summary.done_count}/{summary.total_tasks}")

GitHub:  https://github.com/AV-CSE31/veridian
PyPI:    https://pypi.org/project/veridian-ai/
License: MIT
"""

__version__ = "0.3.0"
__author__ = "Veridian contributors"
__license__ = "MIT"

from veridian.core.exceptions import ProviderError, VeridianError, VerificationError
from veridian.core.task import Task
from veridian.providers.base import LLMProvider, LLMResponse, Message
from veridian.providers.mock_provider import MockProvider
from veridian.verify import builtin as _builtin_verifiers  # noqa: F401
from veridian.verify.base import BaseVerifier, VerificationResult


def __getattr__(name: str) -> object:
    """Lazy-load heavier public primitives only when requested."""
    if name in ("VeridianRunner", "VeridianConfig", "RunSummary"):
        from veridian.core.config import VeridianConfig  # noqa: PLC0415
        from veridian.loop.runner import RunSummary, VeridianRunner  # noqa: PLC0415

        globals()["VeridianRunner"] = VeridianRunner
        globals()["VeridianConfig"] = VeridianConfig
        globals()["RunSummary"] = RunSummary
        return globals()[name]

    if name == "TaskLedger":
        from veridian.ledger.ledger import TaskLedger  # noqa: PLC0415

        globals()["TaskLedger"] = TaskLedger
        return TaskLedger

    if name == "LiteLLMProvider":
        from veridian.providers.litellm_provider import LiteLLMProvider  # noqa: PLC0415

        globals()["LiteLLMProvider"] = LiteLLMProvider
        return LiteLLMProvider

    if name in ("BaseHook", "HookRegistry"):
        from veridian.hooks.base import BaseHook  # noqa: PLC0415
        from veridian.hooks.registry import HookRegistry  # noqa: PLC0415

        globals()["BaseHook"] = BaseHook
        globals()["HookRegistry"] = HookRegistry
        return globals()[name]

    raise AttributeError(f"module 'veridian' has no attribute {name!r}")


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
    "VeridianError",
    "VerificationError",
    "ProviderError",
]
