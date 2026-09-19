"""Core domain models, events, exceptions, and configuration."""

from __future__ import annotations

_LAZY_EXPORTS = {
    "LedgerStats": "chit.core.task:LedgerStats",
    "Task": "chit.core.task:Task",
    "TaskPriority": "chit.core.task:TaskPriority",
    "TaskResult": "chit.core.task:TaskResult",
    "TaskStatus": "chit.core.task:TaskStatus",
    "ChitConfig": "chit.core.config:ChitConfig",
    "ChitError": "chit.core.exceptions:ChitError",
    "VerificationContract": "chit.core.contract:VerificationContract",
    "VerificationDecision": "chit.core.contract:VerificationDecision",
    "VerificationReport": "chit.core.report:VerificationReport",
    "VerifierStep": "chit.core.contract:VerifierStep",
    "verify_completion": "chit.core.contract:verify_completion",
}


def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'chit.core' has no attribute {name!r}")
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
    "ChitError",
    "ChitConfig",
    "VerificationContract",
    "VerificationDecision",
    "VerificationReport",
    "VerifierStep",
    "verify_completion",
]
