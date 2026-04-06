"""Integration tests for tenant isolation hardening."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from veridian.core.task import Task
from veridian.hooks.builtin.identity_guard import IdentityGuardHook
from veridian.integrations.tenancy import (
    TenantAuthz,
    TenantBudget,
    TenantIsolationError,
    TenantRegistry,
)
from veridian.storage.local_json import LocalJSONStorage


@pytest.fixture()
def registry(tmp_path: Path) -> TenantRegistry:
    reg = TenantRegistry()
    reg.register(
        "acme",
        ledger_root=tmp_path,
        budget=TenantBudget(max_tokens=10_000, max_cost_usd=10.0),
    )
    reg.register(
        "globex",
        ledger_root=tmp_path,
        budget=TenantBudget(max_tokens=10_000, max_cost_usd=10.0),
    )
    return reg


@pytest.fixture()
def authz(registry: TenantRegistry) -> TenantAuthz:
    return TenantAuthz(registry)


@pytest.fixture()
def acme_storage(tmp_path: Path) -> LocalJSONStorage:
    storage = LocalJSONStorage(tmp_path / "acme" / "tasks.json")
    storage.put(Task(id="acme::task_1", title="acme task"))
    return storage


def test_tenant_authz_allows_same_tenant(authz: TenantAuthz) -> None:
    assert authz.check_access("acme", "acme") is True


def test_tenant_authz_denies_cross_tenant(authz: TenantAuthz) -> None:
    assert authz.check_access("acme", "globex") is False


def test_tenant_authz_denies_forged_tenant(authz: TenantAuthz) -> None:
    assert authz.check_access("forged", "forged") is False


def test_tenant_authz_single_tenant_mode_compatible(authz: TenantAuthz) -> None:
    assert authz.check_access(None, "acme") is True
    assert authz.check_access("acme", None) is True


def test_registry_blocks_cross_tenant_task_scope(registry: TenantRegistry) -> None:
    with pytest.raises(TenantIsolationError):
        registry.assert_task_in_scope("acme", "globex::task_1")


def test_storage_get_honors_tenant_filter(acme_storage: LocalJSONStorage) -> None:
    task = acme_storage.get("acme::task_1", tenant_id="acme")
    assert task.id == "acme::task_1"
    with pytest.raises(TenantIsolationError):
        acme_storage.get("acme::task_1", tenant_id="globex")


def test_storage_list_all_honors_tenant_filter(acme_storage: LocalJSONStorage) -> None:
    assert len(acme_storage.list_all(tenant_id="acme")) == 1
    assert acme_storage.list_all(tenant_id="globex") == []
    assert len(acme_storage.list_all(tenant_id=None)) == 1


def test_authz_enforce_scope_blocks_cross_tenant(authz: TenantAuthz) -> None:
    with authz.enforce_scope("acme"):
        authz.require_access("acme", "acme")
        with pytest.raises(TenantIsolationError):
            authz.require_access("acme", "globex")


def test_identity_guard_blocks_cross_tenant_context() -> None:
    provider = MagicMock()
    provider.list_refs.return_value = []
    hook = IdentityGuardHook(secrets_provider=provider)

    event = MagicMock()
    event.tenant_id = "acme"
    event.scope_tenant_id = "globex"
    with pytest.raises(TenantIsolationError):
        hook.before_task(event)
