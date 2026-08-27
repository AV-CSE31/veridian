"""Deterministic policy checks with bound implementation identity.

A :class:`Check` is the smallest unit a caller writes by hand. It pairs a plain
Python predicate with the versioned :class:`~veridian.assurance.VerifierManifestV1`
that identifies the exact code and configuration that produced a clause result.

Implementation identity is derived, not asserted: the manifest's ``build_digest``
binds the predicate's module, qualified name, declared version, configuration and
source bytes. Changing the predicate body changes the digest, so a decision cannot
be replayed under a different implementation of the same clause ID.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from veridian.assurance import (
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    VerifierExecutionMode,
    VerifierManifestV1,
    encode_profile_v1,
    sha256_digest,
)

CHECK_IDENTITY_SCHEMA_ID = "veridian.gate.check-identity.v1"
CHECK_IO_SCHEMA_ID = "veridian.gate.check-io.v1"

_INPUT_SCHEMA_DIGEST = sha256_digest(
    encode_profile_v1({"schema_id": CHECK_IO_SCHEMA_ID, "direction": "input"})
)
_OUTPUT_SCHEMA_DIGEST = sha256_digest(
    encode_profile_v1({"schema_id": CHECK_IO_SCHEMA_ID, "direction": "output"})
)


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may read. Checks must not perform I/O."""

    action_type: str
    target: str
    parameters: Mapping[str, object]
    state: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))


@dataclass(frozen=True)
class CheckOutcome:
    """An explicit check result carrying its own reason code and operands.

    Returning this instead of a bare ``bool`` lets a check distinguish
    ``UNKNOWN`` (an input was missing) from ``VIOLATED`` (the rule was broken).
    Under the fail-closed algebra a hard ``UNKNOWN`` yields ``HOLD``, never
    ``ALLOW``.
    """

    status: ClauseStatus
    reason_code: str
    details: Mapping[str, object] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ClauseStatus):
            raise TypeError("CheckOutcome.status must be a ClauseStatus")
        if not self.reason_code or not isinstance(self.reason_code, str):
            raise ValueError("CheckOutcome.reason_code must be a non-empty string")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


CheckPredicate = Callable[[CheckContext], "bool | CheckOutcome"]


def _source_digest(predicate: CheckPredicate) -> str | None:
    """Digest the predicate's source bytes, or ``None`` when unavailable.

    Source is unavailable for predicates defined in a REPL, built by ``exec``,
    or supplied as a C callable. The manifest records that absence rather than
    silently claiming a code binding it does not have.
    """
    try:
        source = inspect.getsource(predicate)
    except (OSError, TypeError):
        return None
    return sha256_digest(source.encode("utf-8"))


def _runtime_identity() -> str:
    version = sys.version_info
    return f"{sys.implementation.name}-{version.major}.{version.minor}"


@dataclass(frozen=True)
class Check:
    """One deterministic clause plus the identity of the code that decides it."""

    clause_id: str
    predicate: CheckPredicate
    reason_code_pass: str = "SATISFIED"
    reason_code_fail: str = "VIOLATED"
    severity: ClauseSeverity = ClauseSeverity.HARD
    version: str = "1.0.0"
    config: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.clause_id or not isinstance(self.clause_id, str):
            raise ValueError("Check.clause_id must be a non-empty string")
        if not callable(self.predicate):
            raise TypeError("Check.predicate must be callable")
        if not isinstance(self.severity, ClauseSeverity):
            raise TypeError("Check.severity must be a ClauseSeverity")
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))

    @property
    def source_bound(self) -> bool:
        """Whether this check's manifest binds the predicate's source bytes."""
        return _source_digest(self.predicate) is not None

    def manifest(self) -> VerifierManifestV1:
        """Build the versioned manifest identifying this check's implementation."""
        source = _source_digest(self.predicate)
        identity = encode_profile_v1(
            {
                "schema_id": CHECK_IDENTITY_SCHEMA_ID,
                "clause_id": self.clause_id,
                "module": getattr(self.predicate, "__module__", "") or "",
                "qualname": getattr(self.predicate, "__qualname__", repr(self.predicate)),
                "semantic_version": self.version,
                "config": dict(self.config),
                "source_digest": source if source is not None else "",
                "source_bound": source is not None,
                "runtime": _runtime_identity(),
            }
        )
        return VerifierManifestV1(
            verifier_id=f"gate.{self.clause_id}",
            semantic_version=self.version,
            build_digest=sha256_digest(identity),
            config={
                **dict(self.config),
                "source_bound": source is not None,
                "runtime": _runtime_identity(),
            },
            input_schema_digest=_INPUT_SCHEMA_DIGEST,
            output_schema_digest=_OUTPUT_SCHEMA_DIGEST,
            deterministic=True,
            execution_mode=VerifierExecutionMode.TRUSTED_IN_PROCESS,
            required_capabilities=(),
            resource_limits={"wall_ms": 1_000},
        )

    def evaluate(self, context: CheckContext) -> tuple[ClauseResultV1, VerifierManifestV1]:
        """Run the predicate fail-closed and bind its verdict to this manifest.

        A predicate that raises becomes ``ERROR``, not a denial and never a pass:
        an exception means the rule did not decide, so the aggregate holds.
        """
        manifest = self.manifest()
        try:
            raw = self.predicate(context)
        except Exception as exc:  # noqa: BLE001 - a failed check must never pass
            outcome = CheckOutcome(
                status=ClauseStatus.ERROR,
                reason_code="CHECK_RAISED",
                details={"exception_type": type(exc).__name__, "message": str(exc)[:200]},
            )
        else:
            outcome = self._normalize(raw)
        return (
            ClauseResultV1(
                clause_id=self.clause_id,
                severity=self.severity,
                status=outcome.status,
                reason_code=outcome.reason_code,
                verifier_manifest_digest=manifest.digest,
                evidence_ids=outcome.evidence_ids,
                details=dict(outcome.details),
            ),
            manifest,
        )

    def _normalize(self, raw: object) -> CheckOutcome:
        if isinstance(raw, CheckOutcome):
            return raw
        if raw is True:
            return CheckOutcome(status=ClauseStatus.SATISFIED, reason_code=self.reason_code_pass)
        if raw is False:
            return CheckOutcome(status=ClauseStatus.VIOLATED, reason_code=self.reason_code_fail)
        return CheckOutcome(
            status=ClauseStatus.ERROR,
            reason_code="CHECK_RETURNED_NON_BOOLEAN",
            details={"returned_type": type(raw).__name__},
        )


def check(
    clause_id: str,
    *,
    severity: ClauseSeverity = ClauseSeverity.HARD,
    version: str = "1.0.0",
    config: Mapping[str, object] | None = None,
    reason_code_pass: str = "SATISFIED",
    reason_code_fail: str = "VIOLATED",
) -> Callable[[CheckPredicate], Check]:
    """Turn a predicate into a :class:`Check`.

    >>> @check("amount_within_limit", config={"limit_minor": 100_000})
    ... def amount_within_limit(ctx):
    ...     return int(ctx.parameters["amount_minor"]) <= 100_000
    """

    def decorate(predicate: CheckPredicate) -> Check:
        return Check(
            clause_id=clause_id,
            predicate=predicate,
            severity=severity,
            version=version,
            config=config or {},
            reason_code_pass=reason_code_pass,
            reason_code_fail=reason_code_fail,
        )

    return decorate
