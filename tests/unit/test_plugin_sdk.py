"""
tests/unit/test_plugin_sdk.py
──────────────────────────────
Tests for WCP-028: Plugin SDK — metadata, decorators, lifecycle hooks.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from veridian.core.exceptions import PluginError
from veridian.core.task import Task, TaskResult
from veridian.plugins.sdk import (
    HookPlugin,
    PluginMetadata,
    VerifierPlugin,
    plugin_manifest,
)
from veridian.verify.base import VerificationResult

# ── PluginMetadata ──────────────────────────────────────────────────────────


class TestPluginMetadata:
    def test_create_verifier_metadata(self) -> None:
        m = PluginMetadata(
            name="acme-lint",
            version="1.0.0",
            author="Acme Corp",
            description="Lint verifier",
            veridian_version_range=">=0.2.0",
            plugin_type="verifier",
        )
        assert m.name == "acme-lint"
        assert m.version == "1.0.0"
        assert m.author == "Acme Corp"
        assert m.description == "Lint verifier"
        assert m.veridian_version_range == ">=0.2.0"
        assert m.plugin_type == "verifier"

    def test_create_hook_metadata(self) -> None:
        m = PluginMetadata(
            name="acme-notify",
            version="0.1.0",
            author="Acme Corp",
            description="Notification hook",
            veridian_version_range=">=0.2.0",
            plugin_type="hook",
        )
        assert m.plugin_type == "hook"

    def test_invalid_plugin_type_raises(self) -> None:
        with pytest.raises(PluginError, match="plugin_type"):
            PluginMetadata(
                name="bad",
                version="1.0.0",
                author="X",
                description="Bad",
                veridian_version_range=">=0.2.0",
                plugin_type="unknown",
            )

    def test_empty_name_raises(self) -> None:
        with pytest.raises(PluginError, match="name"):
            PluginMetadata(
                name="",
                version="1.0.0",
                author="X",
                description="Bad",
                veridian_version_range=">=0.2.0",
                plugin_type="verifier",
            )

    def test_empty_version_raises(self) -> None:
        with pytest.raises(PluginError, match="version"):
            PluginMetadata(
                name="ok",
                version="",
                author="X",
                description="Bad",
                veridian_version_range=">=0.2.0",
                plugin_type="verifier",
            )


# ── VerifierPlugin lifecycle ────────────────────────────────────────────────


class TestVerifierPluginLifecycle:
    def _make_verifier_cls(self) -> type[VerifierPlugin]:
        @plugin_manifest(
            name="test-verifier",
            version="1.0.0",
            author="Test",
            description="A test verifier plugin",
            veridian_version_range=">=0.2.0",
            plugin_type="verifier",
        )
        class MyVerifier(VerifierPlugin):
            id: ClassVar[str] = "test-verifier"
            description: ClassVar[str] = "Test verifier"

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                return VerificationResult(passed=True)

        return MyVerifier

    def test_on_install_called(self) -> None:
        cls = self._make_verifier_cls()
        plugin = cls()
        # on_install should not raise
        plugin.on_install()

    def test_on_remove_called(self) -> None:
        cls = self._make_verifier_cls()
        plugin = cls()
        plugin.on_remove()

    def test_on_upgrade_called(self) -> None:
        cls = self._make_verifier_cls()
        plugin = cls()
        plugin.on_upgrade("0.9.0", "1.0.0")

    def test_metadata_attached(self) -> None:
        cls = self._make_verifier_cls()
        assert hasattr(cls, "plugin_metadata")
        assert cls.plugin_metadata.name == "test-verifier"
        assert cls.plugin_metadata.plugin_type == "verifier"

    def test_is_base_verifier(self) -> None:
        from veridian.verify.base import BaseVerifier

        cls = self._make_verifier_cls()
        assert issubclass(cls, BaseVerifier)


# ── HookPlugin lifecycle ───────────────────────────────────────────────────


class TestHookPluginLifecycle:
    def _make_hook_cls(self) -> type[HookPlugin]:
        @plugin_manifest(
            name="test-hook",
            version="1.0.0",
            author="Test",
            description="A test hook plugin",
            veridian_version_range=">=0.2.0",
            plugin_type="hook",
        )
        class MyHook(HookPlugin):
            id: ClassVar[str] = "test-hook"

            def before_task(self, event: Any) -> None:
                pass

        return MyHook

    def test_on_install_called(self) -> None:
        cls = self._make_hook_cls()
        plugin = cls()
        plugin.on_install()

    def test_on_remove_called(self) -> None:
        cls = self._make_hook_cls()
        plugin = cls()
        plugin.on_remove()

    def test_on_upgrade_called(self) -> None:
        cls = self._make_hook_cls()
        plugin = cls()
        plugin.on_upgrade("0.1.0", "1.0.0")

    def test_metadata_attached(self) -> None:
        cls = self._make_hook_cls()
        assert hasattr(cls, "plugin_metadata")
        assert cls.plugin_metadata.name == "test-hook"
        assert cls.plugin_metadata.plugin_type == "hook"

    def test_is_base_hook(self) -> None:
        from veridian.hooks.base import BaseHook

        cls = self._make_hook_cls()
        assert issubclass(cls, BaseHook)


# ── plugin_manifest decorator ──────────────────────────────────────────────


class TestPluginManifestDecorator:
    def test_attaches_metadata_to_class(self) -> None:
        @plugin_manifest(
            name="deco-test",
            version="2.0.0",
            author="Deco",
            description="Decorator test",
            veridian_version_range=">=0.2.0",
            plugin_type="verifier",
        )
        class Dummy(VerifierPlugin):
            id: ClassVar[str] = "deco-test"
            description: ClassVar[str] = "Deco test"

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                return VerificationResult(passed=True)

        assert Dummy.plugin_metadata.name == "deco-test"
        assert Dummy.plugin_metadata.version == "2.0.0"

    def test_invalid_metadata_raises_on_decoration(self) -> None:
        with pytest.raises(PluginError):

            @plugin_manifest(
                name="",
                version="1.0.0",
                author="X",
                description="Bad",
                veridian_version_range=">=0.2.0",
                plugin_type="verifier",
            )
            class Bad(VerifierPlugin):
                id: ClassVar[str] = "bad"
                description: ClassVar[str] = "Bad"

                def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                    return VerificationResult(passed=True)
