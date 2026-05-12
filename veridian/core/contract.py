"""
veridian.core.contract
──────────────────────
The verification contract a task must satisfy before it can transition to ``DONE``.

This formalizes the prose from ``guides/production/verification-contract.md`` as a
versionable, hashable, serializable dataclass so contracts are first-class artifacts
that operators can audit, diff, and store as evidence.

Core invariant::

    A task must not transition to DONE unless an independent verifier
    accepts the task result. The producing agent must not be the authority
    that marks the work complete.

The contract pins:

* which verifier enforces the contract (``verifier_id``)
* the verifier configuration (frozen at contract time)
* the policy version under which the contract was authored
* failure semantics (``on_failure``: how the runtime should react)
* composition flags (fatal / retryable / human_override / deterministic)

Use ``fingerprint()`` to obtain a stable identifier for audit logs and replay
compatibility checks. Use ``to_dict()`` / ``from_dict()`` for persistence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Final

from veridian.core.exceptions import VeridianConfigError

__all__ = [
    "FAILURE_MODES",
    "VerificationContract",
]

# Failure modes supported by the runtime today. Kept as a constant so the runner
# and integration adapters can validate against the same allowlist.
FAILURE_MODES: Final[frozenset[str]] = frozenset(
    {"raise", "retry", "pause", "dlq", "fail", "abandon"}
)


@dataclass(frozen=True)
class VerificationContract:
    """Formal contract enforced before a task transitions to ``DONE``.

    Args:
        verifier_id: Registered verifier that enforces the contract.
        verifier_config: Frozen, JSON-serializable verifier configuration.
        policy_version: Version-controlled policy identifier (semver, git sha,
            or freeform). Defaults to ``"unversioned"``; production contracts
            should always pin a real value.
        fatal: ``True`` (default) — verifier failure blocks ``DONE``. ``False``
            marks the verifier as advisory: failures are recorded as evidence
            but do not block completion.
        retryable: Whether the runtime may re-run the producing task on failure.
        max_retries: Upper bound on retry attempts when ``retryable=True``. Must
            be ``0`` when ``retryable=False``.
        human_override: Whether a human reviewer can override a verifier
            failure to allow ``DONE``.
        on_failure: Action the runtime takes when ``fatal=True`` and the
            verifier rejects the output. One of :data:`FAILURE_MODES`.
        deterministic: Whether the verifier is expected to be deterministic.
            Non-deterministic verifiers (e.g. LLM judges) cannot be the sole
            authority for irreversible actions.
    """

    verifier_id: str
    verifier_config: Mapping[str, Any] = field(default_factory=dict)
    policy_version: str = "unversioned"
    fatal: bool = True
    retryable: bool = False
    max_retries: int = 0
    human_override: bool = False
    on_failure: str = "raise"
    deterministic: bool = True

    def __post_init__(self) -> None:
        self._validate()

    # ── validation ──────────────────────────────────────────────────────────
    def _validate(self) -> None:
        if not self.verifier_id or not isinstance(self.verifier_id, str):
            raise VeridianConfigError("verifier_id must be a non-empty string")

        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise VeridianConfigError("policy_version must be a non-empty string")

        if self.on_failure not in FAILURE_MODES:
            raise VeridianConfigError(
                f"on_failure={self.on_failure!r} not in {sorted(FAILURE_MODES)}"
            )

        if self.max_retries < 0:
            raise VeridianConfigError("max_retries must be >= 0")

        if not self.retryable and self.max_retries > 0:
            raise VeridianConfigError("max_retries must be 0 when retryable=False")

        if not self.fatal and self.on_failure != "raise":
            # Advisory contracts only record evidence; failure-mode routing is
            # only meaningful when the contract is fatal.
            raise VeridianConfigError(
                "on_failure is only meaningful when fatal=True; "
                "advisory contracts always record-only"
            )

        try:
            # Strict JSON check (no default coercion) — verifier_config must be
            # pure-JSON so contracts can be safely persisted as audit evidence.
            json.dumps(dict(self.verifier_config), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise VeridianConfigError(f"verifier_config must be JSON-serializable: {exc}") from exc

    # ── identity ────────────────────────────────────────────────────────────
    def fingerprint(self) -> str:
        """Return a stable 16-char hex digest identifying this contract.

        Two contracts with identical fields produce the same fingerprint.
        Suitable for audit logs, replay compatibility checks, and contract
        diffs across runs.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ── serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        data = asdict(self)
        data["verifier_config"] = dict(self.verifier_config)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VerificationContract:
        """Construct a contract from a dict, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "verifier_id" not in kwargs:
            raise VeridianConfigError("contract dict missing required key 'verifier_id'")
        return cls(**kwargs)

    # ── ergonomics ──────────────────────────────────────────────────────────
    def with_policy_version(self, policy_version: str) -> VerificationContract:
        """Return a copy of this contract pinned to a new policy version."""
        return replace(self, policy_version=policy_version)
