"""Errors for the industrial banking reference pack."""

from veridian.core.exceptions import VeridianError


class BankingError(VeridianError):
    """Base error for the banking assurance reference pack."""


class BankingValidationError(BankingError):
    """A banking object or trusted binding is invalid."""


class BankingPostconditionError(BankingError):
    """An asserted payment outcome failed postcondition verification."""
