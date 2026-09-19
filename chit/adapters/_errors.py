"""Stable errors raised by protocol adapters."""

from chit.core.exceptions import ChitError


class AdapterError(ChitError):
    """Base error for framework-neutral action adapters."""


class AdapterValidationError(AdapterError):
    """A transport record is malformed, ambiguous, or outside its profile."""


class UnknownActionError(AdapterValidationError):
    """A transport record names an action that has not been registered."""
