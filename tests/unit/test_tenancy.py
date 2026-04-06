"""
tests.unit.test_tenancy
────────────────────────
RV3-012: multi-tenant guardrails — budgets, rate limits, and data isolation.

Acceptance: cross-tenant leakage tests pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.integrations.tenancy import (
    TenantBudget,
    TenantBudgetExceeded,
    TenantIsolationError,
    TenantRateLimit,
    TenantRateLimitExceeded,
    TenantRegistry,
)


@pytest.fixture
def registry(tmp_path: Path) -> TenantRegistry:
    reg = TenantRegistry()
    reg.register(
        "tenant_a",
        ledger_root=tmp_path,
        budget=TenantBudget(max_tokens=1000, max_cost_usd=1.0),
        rate_limit=TenantRateLimit(max_requests=3, window_seconds=60),
    )
    reg.register(
        "tenant_b",
        ledger_root=tmp_path,
        budget=TenantBudget(max_tokens=500, max_cost_usd=0.5),
    )
    return reg


class TestTenantRegistration:
    def test_register_creates_isolated_ledger_paths(
        self, registry: TenantRegistry, tmp_path: Path
    ) -> None:
        a = registry.get("tenant_a")
        b = registry.get("tenant_b")
        assert a.ledger_path != b.ledger_path
        assert a.ledger_path.parent.name == "tenant_a"
        assert b.ledger_path.parent.name == "tenant_b"

    def test_register_rejects_unsafe_tenant_id(self, tmp_path: Path) -> None:
        reg = TenantRegistry()
        with pytest.raises(TenantIsolationError):
            reg.register("../evil", ledger_root=tmp_path, budget=TenantBudget())
        with pytest.raises(TenantIsolationError):
            reg.register("", ledger_root=tmp_path, budget=TenantBudget())
        with pytest.raises(TenantIsolationError):
            reg.register("a/b", ledger_root=tmp_path, budget=TenantBudget())
        with pytest.raises(TenantIsolationError):
            reg.register(r"a\b", ledger_root=tmp_path, budget=TenantBudget())
        with pytest.raises(TenantIsolationError):
            reg.register(r"C:\evil", ledger_root=tmp_path, budget=TenantBudget())

    def test_unknown_tenant_raises(self, registry: TenantRegistry) -> None:
        with pytest.raises(TenantIsolationError):
            registry.get("tenant_ghost")


class TestRateLimit:
    def test_allows_requests_under_limit(self, registry: TenantRegistry) -> None:
        for _ in range(3):
            registry.start_request("tenant_a")

    def test_blocks_requests_over_limit(self, registry: TenantRegistry) -> None:
        for _ in range(3):
            registry.start_request("tenant_a")
        with pytest.raises(TenantRateLimitExceeded) as exc_info:
            registry.start_request("tenant_a")
        assert exc_info.value.tenant_id == "tenant_a"
        assert exc_info.value.limit == 3

    def test_tenant_without_rate_limit_is_unbounded(self, registry: TenantRegistry) -> None:
        for _ in range(20):
            registry.start_request("tenant_b")


class TestBudgetEnforcement:
    def test_charge_accumulates_and_allows_under_budget(self, registry: TenantRegistry) -> None:
        registry.charge("tenant_a", tokens=300, cost_usd=0.2)
        registry.charge("tenant_a", tokens=300, cost_usd=0.2)
        tokens, cost = registry.usage("tenant_a")
        assert tokens == 600
        assert cost == pytest.approx(0.4)

    def test_charge_raises_when_token_budget_exceeded(self, registry: TenantRegistry) -> None:
        registry.charge("tenant_a", tokens=900)
        with pytest.raises(TenantBudgetExceeded) as exc_info:
            registry.charge("tenant_a", tokens=200)
        assert exc_info.value.kind == "tokens"
        assert exc_info.value.tenant_id == "tenant_a"

    def test_charge_raises_when_cost_budget_exceeded(self, registry: TenantRegistry) -> None:
        registry.charge("tenant_b", cost_usd=0.4)
        with pytest.raises(TenantBudgetExceeded) as exc_info:
            registry.charge("tenant_b", cost_usd=0.2)
        assert exc_info.value.kind == "cost_usd"

    def test_zero_limits_are_disabled(self, tmp_path: Path) -> None:
        reg = TenantRegistry()
        reg.register("unbounded", ledger_root=tmp_path, budget=TenantBudget())
        reg.charge("unbounded", tokens=10_000_000, cost_usd=1_000_000)  # no raise


class TestDataIsolation:
    def test_task_in_scope_passes_for_prefixed_ids(self, registry: TenantRegistry) -> None:
        registry.assert_task_in_scope("tenant_a", "tenant_a::task_1")
        registry.assert_task_in_scope("tenant_b", "tenant_b::task_42")

    def test_cross_tenant_task_access_fails_closed(self, registry: TenantRegistry) -> None:
        """Core RV3-012 acceptance: cross-tenant leakage tests pass."""
        with pytest.raises(TenantIsolationError):
            registry.assert_task_in_scope("tenant_a", "tenant_b::task_1")
        with pytest.raises(TenantIsolationError):
            registry.assert_task_in_scope("tenant_b", "tenant_a::task_1")

    def test_unprefixed_task_id_fails_closed(self, registry: TenantRegistry) -> None:
        with pytest.raises(TenantIsolationError):
            registry.assert_task_in_scope("tenant_a", "naked_task_id")


class TestValidation:
    def test_negative_budget_values_rejected(self) -> None:
        with pytest.raises(ValueError):
            TenantBudget(max_tokens=-1)
        with pytest.raises(ValueError):
            TenantBudget(max_cost_usd=-0.01)

    def test_invalid_rate_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            TenantRateLimit(max_requests=0, window_seconds=60)
        with pytest.raises(ValueError):
            TenantRateLimit(max_requests=1, window_seconds=0)
