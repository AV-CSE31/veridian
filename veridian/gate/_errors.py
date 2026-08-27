"""Errors raised by the gate porcelain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from veridian.core.exceptions import VeridianError

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checking only
    from ._gate import Verdict


class GateError(VeridianError):
    """Base class for gate porcelain failures."""


class GateConfigurationError(GateError):
    """The gate was constructed with an unusable policy or key configuration."""


class GateRefusedError(GateError):
    """A guarded call did not reach execution. Carries the verdict that stopped it."""

    def __init__(self, message: str, *, verdict: Verdict | None = None) -> None:
        super().__init__(message)
        self.verdict = verdict


class GateDeniedError(GateRefusedError):
    """A hard clause was violated; the action is refused."""


class GateHeldError(GateRefusedError):
    """A hard clause could not decide; the action is held rather than allowed."""
