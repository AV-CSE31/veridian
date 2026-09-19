"""Errors raised at the trusted effect boundary."""

from chit.core.exceptions import ChitError


class EffectError(ChitError):
    """Base class for effect authorization and execution failures."""


class EffectValidationError(EffectError):
    """An effect event or trajectory violates the versioned protocol."""


class EffectExecutionError(EffectError):
    """The trusted executor could not dispatch or record an effect."""


class PermitError(EffectError):
    """A permit cannot be issued, validated, or redeemed."""


class PermitReplayError(PermitError):
    """A single-use permit was already redeemed."""
