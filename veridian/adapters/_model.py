"""Public adapter contracts."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from veridian.assurance import ActionSemanticsV1, TransportBinding

from ._errors import AdapterValidationError


def _require_profile_name(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterValidationError(f"{field_name} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise AdapterValidationError(f"{field_name} must use NFC Unicode")
    return value


@dataclass(frozen=True)
class ActionSpecV1:
    """Map one registered tool name to a business action and target argument.

    The target argument stays in ``parameters`` as well as identifying the
    ``ActionSemanticsV1.target``. No business argument is discarded or renamed.
    """

    action_type: str
    target_parameter: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "action_type", _require_profile_name(self.action_type, "action_type")
        )
        object.__setattr__(
            self,
            "target_parameter",
            _require_profile_name(self.target_parameter, "target_parameter"),
        )


@dataclass(frozen=True)
class NormalizedActionV1:
    """Business semantics plus independent transport provenance."""

    semantics: ActionSemanticsV1
    transport: TransportBinding


@runtime_checkable
class ActionAdapter(Protocol):
    """Framework-neutral seam for deterministic, non-executing normalization."""

    def normalize(self, message: object) -> NormalizedActionV1:
        """Validate a wire record and return separated semantics and provenance."""
        ...
