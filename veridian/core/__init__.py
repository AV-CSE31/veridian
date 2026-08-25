"""Core domain models, events, exceptions, and configuration."""

from __future__ import annotations

_LAZY_EXPORTS = {
    "LedgerStats": "veridian.core.task:LedgerStats",
    "Task": "veridian.core.task:Task",
    "TaskPriority": "veridian.core.task:TaskPriority",
    "TaskResult": "veridian.core.task:TaskResult",
    "TaskStatus": "veridian.core.task:TaskStatus",
    "VeridianConfig": "veridian.core.config:VeridianConfig",
    "VeridianError": "veridian.core.exceptions:VeridianError",
    "VerificationContract": "veridian.core.contract:VerificationContract",
    "VerificationDecision": "veridian.core.contract:VerificationDecision",
    "VerificationReport": "veridian.core.report:VerificationReport",
    "VerifierStep": "veridian.core.contract:VerifierStep",
    "verify_completion": "veridian.core.contract:verify_completion",
}


def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'veridian.core' has no attribute {name!r}")
    module_name, attr_name = target.rsplit(":", 1)
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "Task",
    "TaskStatus",
    "TaskResult",
    "TaskPriority",
    "LedgerStats",
    "VeridianError",
    "VeridianConfig",
    "VerificationContract",
    "VerificationDecision",
    "VerificationReport",
    "VerifierStep",
    "verify_completion",
]
