"""
tests/unit/test_plugin_registry.py
────────────────────────────────────
Tests for WCP-029: Plugin Registry — discover, load, unload, version resolution.
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from veridian.core.exceptions import PluginError
from veridian.core.task import Task, TaskResult
from veridian.plugins.registry import PluginRegistry
from veridian.plugins.sdk import (
    HookPlugin,
    VerifierPlugin,
    plugin_manifest,
)
from veridian.verify.base import VerificationResult

# ── Fixtures ────────────────────────────────────────────────────────────────


@plugin_manifest(
    name="fixture-verifier",
    version="1.0.0",
    author="Test",
    description="Fixture verifier",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class FixtureVerifier(VerifierPlugin):
    id: ClassVar[str] = "fixture-verifier"
    description: ClassVar[str] = "Fixture verifier"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


@plugin_manifest(
    name="fixture-verifier",
    version="2.0.0",
    author="Test",
    description="Fixture verifier v2",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class FixtureVerifierV2(VerifierPlugin):
    id: ClassVar[str] = "fixture-verifier"
    description: ClassVar[str] = "Fixture verifier v2"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


@plugin_manifest(
    name="fixture-hook",
    version="1.0.0",
    author="Test",
    description="Fixture hook",
    veridian_version_range=">=0.2.0",
    plugin_type="hook",
)
class FixtureHook(HookPlugin):
    id: ClassVar[str] = "fixture-hook"

    def before_task(self, event: Any) -> None:
        pass


# ── Discover ────────────────────────────────────────────────────────────────


class TestDiscover:
    def test_discover_finds_plugins_via_entry_points(self) -> None:
        registry = PluginRegistry()
        mock_ep = MagicMock()
        mock_ep.name = "fixture-verifier"
        mock_ep.load.return_value = FixtureVerifier

        with patch("veridian.plugins.registry.entry_points", return_value=[mock_ep]):
            found = registry.discover()

        assert len(found) >= 1
        assert any(m.name == "fixture-verifier" for m in found)

    def test_discover_returns_empty_for_no_plugins(self) -> None:
        registry = PluginRegistry()
        with patch("veridian.plugins.registry.entry_points", return_value=[]):
            found = registry.discover()
        assert found == []

    def test_discover_skips_broken_entry_point(self) -> None:
        registry = PluginRegistry()
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("boom")

        with patch("veridian.plugins.registry.entry_points", return_value=[mock_ep]):
            found = registry.discover()
        assert found == []


# ── Load ────────────────────────────────────────────────────────────────────


class TestLoad:
    def test_load_instantiates_verifier_plugin(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {"1.0.0": FixtureVerifier}

        plugin = registry.load("fixture-verifier")
        assert isinstance(plugin, FixtureVerifier)

    def test_load_with_specific_version(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {
            "1.0.0": FixtureVerifier,
            "2.0.0": FixtureVerifierV2,
        }

        plugin = registry.load("fixture-verifier", version="2.0.0")
        assert isinstance(plugin, FixtureVerifierV2)

    def test_load_latest_when_no_version_specified(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {
            "1.0.0": FixtureVerifier,
            "2.0.0": FixtureVerifierV2,
        }

        plugin = registry.load("fixture-verifier")
        assert isinstance(plugin, FixtureVerifierV2)

    def test_load_nonexistent_raises(self) -> None:
        registry = PluginRegistry()
        with pytest.raises(PluginError, match="not found"):
            registry.load("nonexistent")

    def test_load_nonexistent_version_raises(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {"1.0.0": FixtureVerifier}

        with pytest.raises(PluginError, match="version"):
            registry.load("fixture-verifier", version="99.0.0")


# ── Unload ──────────────────────────────────────────────────────────────────


class TestUnload:
    def test_unload_removes_plugin(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {"1.0.0": FixtureVerifier}
        registry.load("fixture-verifier")
        assert "fixture-verifier" in registry._loaded

        registry.unload("fixture-verifier")
        assert "fixture-verifier" not in registry._loaded

    def test_unload_nonloaded_raises(self) -> None:
        registry = PluginRegistry()
        with pytest.raises(PluginError, match="not loaded"):
            registry.unload("nonexistent")


# ── Duplicate ───────────────────────────────────────────────────────────────


class TestDuplicate:
    def test_load_duplicate_raises(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {"1.0.0": FixtureVerifier}
        registry.load("fixture-verifier")

        with pytest.raises(PluginError, match="already loaded"):
            registry.load("fixture-verifier")


# ── List ────────────────────────────────────────────────────────────────────


class TestListPlugins:
    def test_list_plugins_returns_metadata(self) -> None:
        registry = PluginRegistry()
        registry._available["fixture-verifier"] = {"1.0.0": FixtureVerifier}
        registry._available["fixture-hook"] = {"1.0.0": FixtureHook}

        plugins = registry.list_plugins()
        names = [m.name for m in plugins]
        assert "fixture-verifier" in names
        assert "fixture-hook" in names

    def test_list_empty(self) -> None:
        registry = PluginRegistry()
        assert registry.list_plugins() == []
