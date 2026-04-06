"""
veridian.integrations.tenancy
──────────────────────────────
RV3-012: Multi-tenant runtime guardrails.

Small, deterministic tenant isolation layer that adapters can attach to a
``RunContext`` to enforce:

- Per-tenant cost budgets (tokens + USD)
- Per-tenant rate limits (requests per window)
- Per-tenant data isolation via ledger path scoping + task-id prefixing
- Cross-tenant access refusal (fail-closed)

The module is intentionally framework-agnostic. It does not talk to any
external billing system — it just tracks in-memory counters that adapters
can reset or persist elsewhere. The guardrails raise ``TenantBudgetExceeded``
or ``TenantRateLimitExceeded`` when a limit is crossed; runners treat these
as control-flow failures, never silent drops.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from threading import local as _thread_local

from veridian.core.exceptions import VeridianError

__all__ = [
    "TenantAuthz",
    "TenantBudget",
    "TenantRateLimit",
    "TenantIsolationError",
    "TenantBudgetExceeded",
    "TenantRateLimitExceeded",
    "TenantRegistry",
    "TenantScope",
]


class TenantIsolationError(VeridianError):
    """Raised when a tenant attempts to access a resource outside its scope."""


class TenantBudgetExceeded(VeridianError):
    def __init__(self, tenant_id: str, kind: str, current: float, limit: float) -> None:
        self.tenant_id = tenant_id
        self.kind = kind
        self.current = current
        self.limit = limit
        super().__init__(
            f"Tenant {tenant_id!r} {kind} budget exceeded: {current:.4g} > {limit:.4g}"
        )


class TenantRateLimitExceeded(VeridianError):
    def __init__(self, tenant_id: str, window_seconds: float, limit: int, current: int) -> None:
        self.tenant_id = tenant_id
        self.window_seconds = window_seconds
        self.limit = limit
        self.current = current
        super().__init__(
            f"Tenant {tenant_id!r} rate limit exceeded: "
            f"{current} requests in last {window_seconds}s (limit {limit})"
        )


@dataclass(frozen=True, slots=True)
class TenantBudget:
    """Per-tenant cost + token ceilings."""

    max_tokens: int = 0  # 0 disables the check
    max_cost_usd: float = 0.0  # 0 disables the check

    def __post_init__(self) -> None:
        if self.max_tokens < 0:
            raise ValueError("max_tokens must be >= 0")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0")


@dataclass(frozen=True, slots=True)
class TenantRateLimit:
    """Per-tenant request-rate ceiling inside a rolling window."""

    max_requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")


@dataclass
class _TenantUsage:
    """Mutable counters tracked per tenant inside the registry."""

    tokens: int = 0
    cost_usd: float = 0.0
    request_timestamps: list[float] = field(default_factory=list)


@dataclass
class TenantScope:
    """Bound execution scope for a single tenant request.

    Attached to a ``RunContext`` via ``ctx.metadata['tenant_scope']`` by the
    adapter. All downstream cost/rate checks consult this scope.
    """

    tenant_id: str
    ledger_path: Path
    task_prefix: str
    budget: TenantBudget
    rate_limit: TenantRateLimit | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class TenantRegistry:
    """In-memory registry of tenants, budgets, usage, and rate-limit state.

    Adapters create a single registry per deployment and call
    ``start_request`` before each tenant-scoped operation. The registry
    enforces the budget + rate limit and returns the tenant scope used by
    downstream SDK calls.
    """

    def __init__(self) -> None:
        self._tenants: dict[str, TenantScope] = {}
        self._usage: dict[str, _TenantUsage] = {}

    def register(
        self,
        tenant_id: str,
        *,
        ledger_root: Path,
        budget: TenantBudget,
        rate_limit: TenantRateLimit | None = None,
        metadata: dict[str, str] | None = None,
    ) -> TenantScope:
        """Register a tenant. Tenant-scoped ledger path is derived from
        ``ledger_root/<tenant_id>/ledger.json`` to guarantee file-level
        isolation between tenants — no cross-tenant data leakage.
        """
        parts = [part for part in re.split(r"[\\/]+", tenant_id) if part]
        invalid = (
            not tenant_id
            or ":" in tenant_id
            or any(part in {".", ".."} for part in parts)
            or any(sep in tenant_id for sep in ("/", "\\"))
        )
        if invalid:
            raise TenantIsolationError(
                f"Invalid tenant_id: {tenant_id!r}; must be non-empty and path-safe"
            )

        root_dir = ledger_root.resolve()
        tenant_dir = (root_dir / tenant_id).resolve()
        if not tenant_dir.is_relative_to(root_dir):
            raise TenantIsolationError(
                f"Invalid tenant_id: {tenant_id!r}; must stay under ledger_root"
            )

        tenant_dir.mkdir(parents=True, exist_ok=True)
        scope = TenantScope(
            tenant_id=tenant_id,
            ledger_path=tenant_dir / "ledger.json",
            task_prefix=f"{tenant_id}::",
            budget=budget,
            rate_limit=rate_limit,
            metadata=dict(metadata or {}),
        )
        self._tenants[tenant_id] = scope
        self._usage[tenant_id] = _TenantUsage()
        return scope

    def get(self, tenant_id: str) -> TenantScope:
        scope = self._tenants.get(tenant_id)
        if scope is None:
            raise TenantIsolationError(f"Unknown tenant: {tenant_id!r}")
        return scope

    def start_request(self, tenant_id: str) -> TenantScope:
        """Record a request and enforce the rate limit.

        Raises ``TenantRateLimitExceeded`` when the tenant has exceeded its
        windowed request ceiling. Must be called at the beginning of every
        tenant-scoped operation.
        """
        scope = self.get(tenant_id)
        usage = self._usage[tenant_id]

        if scope.rate_limit is not None:
            now = time.monotonic()
            cutoff = now - scope.rate_limit.window_seconds
            usage.request_timestamps = [t for t in usage.request_timestamps if t >= cutoff]
            if len(usage.request_timestamps) >= scope.rate_limit.max_requests:
                raise TenantRateLimitExceeded(
                    tenant_id=tenant_id,
                    window_seconds=scope.rate_limit.window_seconds,
                    limit=scope.rate_limit.max_requests,
                    current=len(usage.request_timestamps),
                )
            usage.request_timestamps.append(now)
        return scope

    def charge(self, tenant_id: str, *, tokens: int = 0, cost_usd: float = 0.0) -> None:
        """Record usage and enforce the cost budget.

        Raises ``TenantBudgetExceeded`` when the new totals cross the budget.
        Adapters call this after each activity (e.g. after an LLM response
        returns token counts).
        """
        scope = self.get(tenant_id)
        usage = self._usage[tenant_id]
        usage.tokens += max(0, tokens)
        usage.cost_usd += max(0.0, cost_usd)

        if scope.budget.max_tokens and usage.tokens > scope.budget.max_tokens:
            raise TenantBudgetExceeded(
                tenant_id=tenant_id,
                kind="tokens",
                current=usage.tokens,
                limit=scope.budget.max_tokens,
            )
        if scope.budget.max_cost_usd and usage.cost_usd > scope.budget.max_cost_usd:
            raise TenantBudgetExceeded(
                tenant_id=tenant_id,
                kind="cost_usd",
                current=usage.cost_usd,
                limit=scope.budget.max_cost_usd,
            )

    def usage(self, tenant_id: str) -> tuple[int, float]:
        """Return (tokens_used, cost_usd_used) for diagnostics."""
        u = self._usage.get(tenant_id)
        if u is None:
            raise TenantIsolationError(f"Unknown tenant: {tenant_id!r}")
        return u.tokens, u.cost_usd

    def assert_task_in_scope(self, tenant_id: str, task_id: str) -> None:
        """Fail-closed check that a task id belongs to the given tenant.

        Every tenant-scoped task must have an id starting with the tenant's
        prefix. Adapters call this before mutating any ledger state so a
        misrouted request cannot touch another tenant's data.
        """
        scope = self.get(tenant_id)
        if not task_id.startswith(scope.task_prefix):
            raise TenantIsolationError(f"Task {task_id!r} is not in scope for tenant {tenant_id!r}")


class TenantAuthz:
    """Runtime authorization gate for tenant boundary enforcement.

    Provides both a boolean check (``check_access``) and a raising variant
    (``require_access``) plus a context-manager ``enforce_scope`` that sets
    a thread-local tenant restriction for the duration of a block.

    Rules:
      - ``tenant_id=None`` means single-tenant / no multi-tenancy -> always allow.
      - ``resource_tenant=None`` means the resource has no tenant scope -> allow.
      - Same tenant -> allow.
      - Different registered tenants -> deny.
      - Unregistered (forged) tenant_id -> deny (fail-closed).
    """

    def __init__(self, registry: TenantRegistry) -> None:
        self._registry = registry
        self._local: _thread_local = _thread_local()

    def check_access(self, tenant_id: str | None, resource_tenant: str | None) -> bool:
        """Return True if the caller tenant may access the resource tenant.

        Returns True when:
          - ``tenant_id`` is None (single-tenant mode, no restriction)
          - ``resource_tenant`` is None (unscoped resource)
          - Both are the same string **and** tenant_id is registered

        Returns False otherwise (fail-closed).
        """
        if tenant_id is None or resource_tenant is None:
            return True
        if tenant_id != resource_tenant:
            return False
        # Verify the tenant actually exists in the registry (forged-id check)
        try:
            self._registry.get(tenant_id)
        except TenantIsolationError:
            return False
        return True

    def require_access(self, tenant_id: str | None, resource_tenant: str | None) -> None:
        """Like ``check_access`` but raises ``TenantIsolationError`` on denial."""
        if not self.check_access(tenant_id, resource_tenant):
            raise TenantIsolationError(
                f"Tenant {tenant_id!r} denied access to resource owned by {resource_tenant!r}"
            )

    @contextlib.contextmanager
    def enforce_scope(self, tenant_id: str | None) -> Generator[None, None, None]:
        """Context manager that restricts all operations to *tenant_id*.

        While inside the block, ``require_access`` and ``check_access`` honour
        the active scope.  When *tenant_id* is ``None`` the scope is a no-op
        (single-tenant backward compatibility).
        """
        prev: str | None = getattr(self._local, "active_tenant", None)
        self._local.active_tenant = tenant_id
        try:
            yield
        finally:
            self._local.active_tenant = prev
