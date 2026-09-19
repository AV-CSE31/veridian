"""Errors for the industrial banking reference pack."""

from chit.core.exceptions import ChitError


class BankingError(ChitError):
    """Base error for the banking assurance reference pack."""


class BankingValidationError(BankingError):
    """A banking object or trusted binding is invalid."""


class BankingPostconditionError(BankingError):
    """An asserted payment outcome failed postcondition verification."""
