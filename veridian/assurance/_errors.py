"""Errors exposed by the assurance-kernel interface."""

from veridian.core.exceptions import VeridianError


class AssuranceError(VeridianError):
    """Base error for assurance contracts, artifacts, and policies."""


class AssuranceValidationError(AssuranceError):
    """An assurance object is outside its versioned profile."""


class AssuranceVerificationError(AssuranceError):
    """A proof or cryptographic object failed verification."""


class AssuranceDependencyError(AssuranceError):
    """An explicitly selected assurance implementation is unavailable."""


class AssurancePolicyError(AssuranceError):
    """A requested operation is forbidden by assurance policy."""
