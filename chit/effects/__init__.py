"""Trusted side-effect authorization, execution, and audit primitives."""

from ._attestation import (
    VerifiedEffectReceipt,
    VerifiedExecutionPermit,
    sign_effect_receipt,
    sign_execution_permit,
    verify_effect_receipt,
    verify_execution_permit,
)
from ._errors import (
    EffectError,
    EffectExecutionError,
    EffectValidationError,
    PermitError,
    PermitReplayError,
)
from ._events import EffectEventType, EffectEventV1, EffectState, EffectStatus, reduce_effects
from ._executor import (
    DispatchRequest,
    DispatchResult,
    EffectAdapter,
    ExecutionOutcome,
    TrustedExecutor,
)
from ._permit import ExecutionPermitV1
from ._receipt import EffectReceiptType, EffectReceiptV1
from ._store import OutboxRecord, OutboxStatus, SqlitePermitStore

__all__ = [
    "DispatchRequest",
    "DispatchResult",
    "EffectAdapter",
    "EffectError",
    "EffectExecutionError",
    "EffectEventType",
    "EffectEventV1",
    "EffectReceiptType",
    "EffectReceiptV1",
    "EffectState",
    "EffectStatus",
    "EffectValidationError",
    "ExecutionPermitV1",
    "ExecutionOutcome",
    "OutboxRecord",
    "OutboxStatus",
    "PermitError",
    "PermitReplayError",
    "SqlitePermitStore",
    "TrustedExecutor",
    "VerifiedEffectReceipt",
    "VerifiedExecutionPermit",
    "reduce_effects",
    "sign_effect_receipt",
    "sign_execution_permit",
    "verify_effect_receipt",
    "verify_execution_permit",
]
