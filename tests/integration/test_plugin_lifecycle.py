"""
tests/integration/test_plugin_lifecycle.py
────────────────────────────────────────────
E2E: publish -> discover -> load -> verify -> unload full cycle.
"""

from __future__ import annotations

from typing import Any, ClassVar

from veridian.core.task import Task, TaskResult
from veridian.plugins.certification import CertificationSuite
from veridian.plugins.marketplace import MarketplaceEntry, MarketplaceIndex
from veridian.plugins.registry import PluginRegistry
from veridian.plugins.sdk import (
    HookPlugin,
    VerifierPlugin,
    plugin_manifest,
)
from veridian.verify.base import VerificationResult

# ── Test fixtures ───────────────────────────────────────────────────────────


@plugin_manifest(
    name="lifecycle-verifier",
    version="1.0.0",
    author="Integration",
    description="Lifecycle test verifier",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class LifecycleVerifier(VerifierPlugin):
    id: ClassVar[str] = "lifecycle-verifier"
    description: ClassVar[str] = "Lifecycle test"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True, evidence={"lifecycle": "ok"})


@plugin_manifest(
    name="lifecycle-hook",
    version="1.0.0",
    author="Integration",
    description="Lifecycle test hook",
    veridian_version_range=">=0.2.0",
    plugin_type="hook",
)
class LifecycleHook(HookPlugin):
    id: ClassVar[str] = "lifecycle-hook"

    def before_task(self, event: Any) -> None:
        pass


# ── Full lifecycle ──────────────────────────────────────────────────────────


class TestPluginFullLifecycle:
    def test_publish_discover_load_verify_unload(self) -> None:
        # 1. Certify
        suite = CertificationSuite()
        cert_result = suite.certify(LifecycleVerifier)
        assert cert_result.passed is True

        # 2. Publish to marketplace
        marketplace = MarketplaceIndex()
        entry = MarketplaceEntry(
            metadata=LifecycleVerifier.plugin_metadata,
            certification_status="certified",
            trust_score=0.95,
        )
        marketplace.publish(entry)

        # 3. Search in marketplace
        found = marketplace.search("lifecycle")
        assert len(found) == 1
        assert found[0].metadata.name == "lifecycle-verifier"

        # 4. Load via registry
        registry = PluginRegistry()
        registry._available["lifecycle-verifier"] = {"1.0.0": LifecycleVerifier}

        plugin = registry.load("lifecycle-verifier")
        assert isinstance(plugin, LifecycleVerifier)

        # 5. Verify something
        task = Task(
            title="Integration test task",
            description="Test",
            verifier_id="lifecycle-verifier",
        )
        result = TaskResult(raw_output="ok")
        vr = plugin.verify(task, result)
        assert vr.passed is True
        assert vr.evidence["lifecycle"] == "ok"

        # 6. Unload
        registry.unload("lifecycle-verifier")
        assert "lifecycle-verifier" not in registry._loaded

    def test_hook_plugin_lifecycle(self) -> None:
        registry = PluginRegistry()
        registry._available["lifecycle-hook"] = {"1.0.0": LifecycleHook}

        plugin = registry.load("lifecycle-hook")
        assert isinstance(plugin, LifecycleHook)

        # Fire lifecycle
        plugin.on_install()
        plugin.before_task({"task_id": "test-123"})
        plugin.on_remove()

        registry.unload("lifecycle-hook")
        assert "lifecycle-hook" not in registry._loaded

    def test_marketplace_list_all(self) -> None:
        marketplace = MarketplaceIndex()
        entry_v = MarketplaceEntry(
            metadata=LifecycleVerifier.plugin_metadata,
            certification_status="certified",
            trust_score=0.9,
        )
        entry_h = MarketplaceEntry(
            metadata=LifecycleHook.plugin_metadata,
            certification_status="pending",
            trust_score=0.5,
        )
        marketplace.publish(entry_v)
        marketplace.publish(entry_h)

        all_entries = marketplace.list_all()
        assert len(all_entries) == 2
