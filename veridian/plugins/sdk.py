"""
veridian.plugins.sdk
─────────────────────
Plugin SDK primitives — metadata, base classes, and decorators for
building Veridian plugins (verifiers and hooks).

Third-party developers subclass VerifierPlugin or HookPlugin and
decorate with @plugin_manifest to attach metadata.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from veridian.core.exceptions import PluginError
from veridian.core.task import Task, TaskResult
from veridian.hooks.base import BaseHook
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = [
    "HookPlugin",
    "PluginMetadata",
    "VerifierPlugin",
    "plugin_manifest",
]

log = logging.getLogger(__name__)

_VALID_PLUGIN_TYPES = frozenset({"verifier", "hook"})


# ── Metadata ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginMetadata:
    """Immutable metadata for a Veridian plugin.

    Validated on creation — invalid metadata raises PluginError immediately.
    """

    name: str
    version: str
    author: str
    description: str
    veridian_version_range: str
    plugin_type: str  # "verifier" | "hook"

    def __post_init__(self) -> None:
        if not self.name:
            raise PluginError("PluginMetadata.name must not be empty")
        if not self.version:
            raise PluginError("PluginMetadata.version must not be empty")
        if self.plugin_type not in _VALID_PLUGIN_TYPES:
            raise PluginError(
                f"PluginMetadata.plugin_type must be one of {sorted(_VALID_PLUGIN_TYPES)}, "
                f"got {self.plugin_type!r}"
            )


# ── VerifierPlugin ──────────────────────────────────────────────────────────


class VerifierPlugin(BaseVerifier):
    """Base class for plugin verifiers.

    Subclasses MUST:
    1. Define a class-level ``id`` (inherited from BaseVerifier).
    2. Implement ``verify(task, result) -> VerificationResult``.
    3. Be decorated with ``@plugin_manifest(...)`` or set ``plugin_metadata``
       directly as a ClassVar.

    Lifecycle hooks (on_install, on_remove, on_upgrade) are optional.
    """

    id: ClassVar[str] = "plugin_verifier_base"
    plugin_metadata: ClassVar[PluginMetadata]

    def on_install(self) -> None:
        """Called once when the plugin is first loaded into a registry."""
        log.debug("plugin.on_install name=%s", getattr(self, "plugin_metadata", None))

    def on_remove(self) -> None:
        """Called when the plugin is unloaded from a registry."""
        log.debug("plugin.on_remove name=%s", getattr(self, "plugin_metadata", None))

    def on_upgrade(self, from_version: str, to_version: str) -> None:
        """Called when the plugin is upgraded from one version to another."""
        log.debug(
            "plugin.on_upgrade name=%s from=%s to=%s",
            getattr(self, "plugin_metadata", None),
            from_version,
            to_version,
        )

    @abstractmethod
    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Subclasses must override this."""


# ── HookPlugin ──────────────────────────────────────────────────────────────


class HookPlugin(BaseHook):
    """Base class for plugin hooks.

    Subclasses MUST:
    1. Define a class-level ``id`` (inherited from BaseHook).
    2. Override at least one lifecycle method (before_task, after_task, etc.).
    3. Be decorated with ``@plugin_manifest(...)`` or set ``plugin_metadata``
       directly as a ClassVar.

    Plugin lifecycle hooks (on_install, on_remove, on_upgrade) are optional.
    """

    id: ClassVar[str] = "plugin_hook_base"
    plugin_metadata: ClassVar[PluginMetadata]

    def on_install(self) -> None:
        """Called once when the plugin is first loaded into a registry."""
        log.debug("plugin.on_install name=%s", getattr(self, "plugin_metadata", None))

    def on_remove(self) -> None:
        """Called when the plugin is unloaded from a registry."""
        log.debug("plugin.on_remove name=%s", getattr(self, "plugin_metadata", None))

    def on_upgrade(self, from_version: str, to_version: str) -> None:
        """Called when the plugin is upgraded from one version to another."""
        log.debug(
            "plugin.on_upgrade name=%s from=%s to=%s",
            getattr(self, "plugin_metadata", None),
            from_version,
            to_version,
        )


# ── Decorator ───────────────────────────────────────────────────────────────


def plugin_manifest(
    *,
    name: str,
    version: str,
    author: str,
    description: str,
    veridian_version_range: str,
    plugin_type: str,
) -> Any:
    """Class decorator that attaches validated PluginMetadata to a plugin class.

    Usage::

        @plugin_manifest(
            name="acme-lint",
            version="1.0.0",
            author="Acme Corp",
            description="Custom lint verifier",
            veridian_version_range=">=0.2.0",
            plugin_type="verifier",
        )
        class AcmeLintVerifier(VerifierPlugin):
            id: ClassVar[str] = "acme-lint"
            ...
    """
    # Validate eagerly — fail on decoration, not at runtime
    metadata = PluginMetadata(
        name=name,
        version=version,
        author=author,
        description=description,
        veridian_version_range=veridian_version_range,
        plugin_type=plugin_type,
    )

    def decorator(cls: type[Any]) -> type[Any]:
        cls.plugin_metadata = metadata
        return cls

    return decorator
