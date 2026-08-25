"""Stable errors raised by protocol adapters."""

from veridian.core.exceptions import VeridianError


class AdapterError(VeridianError):
    """Base error for framework-neutral action adapters."""


class AdapterValidationError(AdapterError):
    """A transport record is malformed, ambiguous, or outside its profile."""


class UnknownActionError(AdapterValidationError):
    """A transport record names an action that has not been registered."""
