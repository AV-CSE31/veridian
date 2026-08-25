"""Errors raised at the trusted effect boundary."""

from veridian.core.exceptions import VeridianError


class EffectError(VeridianError):
    """Base class for effect authorization and execution failures."""


class EffectValidationError(EffectError):
    """An effect event or trajectory violates the versioned protocol."""


class EffectExecutionError(EffectError):
    """The trusted executor could not dispatch or record an effect."""


class PermitError(EffectError):
    """A permit cannot be issued, validated, or redeemed."""


class PermitReplayError(PermitError):
    """A single-use permit was already redeemed."""
