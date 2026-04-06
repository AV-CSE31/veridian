"""
veridian.plugins.registry
──────────────────────────
PluginRegistry — discover, load, unload, and list Veridian plugins.

Plugins are discovered via ``importlib.metadata.entry_points``
(group ``veridian.plugins``) and can be loaded/unloaded at runtime.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from veridian.core.exceptions import PluginError
from veridian.plugins.sdk import HookPlugin, PluginMetadata, VerifierPlugin

__all__ = [
    "PluginRegistry",
]

log = logging.getLogger(__name__)

PluginClass = type[VerifierPlugin] | type[HookPlugin]
PluginInstance = VerifierPlugin | HookPlugin


class PluginRegistry:
    """Manages available and loaded Veridian plugins.

    Attributes
    ----------
    _available : dict mapping plugin name -> {version -> class}
        All discovered/registered plugin classes, keyed by name then version.
    _loaded : dict mapping plugin name -> instantiated plugin
        Currently active plugin instances.
    """

    def __init__(self) -> None:
        self._available: dict[str, dict[str, PluginClass]] = {}
        self._loaded: dict[str, PluginInstance] = {}

    # ── Discover ────────────────────────────────────────────────────────

    def discover(self) -> list[PluginMetadata]:
        """Discover plugins via ``importlib.metadata`` entry points.

        Returns a list of PluginMetadata for each successfully discovered plugin.
        Broken entry points are logged and skipped.
        """
        found: list[PluginMetadata] = []
        try:
            eps = entry_points(group="veridian.plugins")
        except Exception as exc:
            log.warning("plugin.discover entry_points failed: %s", exc)
            return found

        for ep in eps:
            try:
                cls = ep.load()
                metadata: PluginMetadata = cls.plugin_metadata
                name = metadata.name
                version = metadata.version

                if name not in self._available:
                    self._available[name] = {}
                self._available[name][version] = cls

                found.append(metadata)
                log.info("plugin.discover name=%s version=%s", name, version)
            except Exception as exc:
                log.warning("plugin.discover failed ep=%s err=%s", ep.name, exc)

        return found

    # ── Load ────────────────────────────────────────────────────────────

    def load(self, name: str, version: str | None = None) -> PluginInstance:
        """Instantiate and activate a plugin by name.

        Parameters
        ----------
        name : str
            Plugin name (must be in ``_available``).
        version : str, optional
            Specific version to load. If None, loads the latest (highest
            semver string via sorted()).

        Returns
        -------
        PluginInstance
            The instantiated plugin.

        Raises
        ------
        PluginError
            If plugin not found, version not found, or already loaded.
        """
        if name in self._loaded:
            raise PluginError(f"Plugin {name!r} is already loaded")

        versions = self._available.get(name)
        if not versions:
            raise PluginError(f"Plugin {name!r} not found in available plugins")

        if version is not None:
            cls = versions.get(version)
            if cls is None:
                available_versions = sorted(versions.keys())
                raise PluginError(
                    f"Plugin {name!r} version {version!r} not found. "
                    f"Available versions: {available_versions}"
                )
        else:
            # Pick latest version (lexicographic sort works for semver major.minor.patch)
            latest = sorted(versions.keys())[-1]
            cls = versions[latest]

        instance = cls()
        instance.on_install()
        self._loaded[name] = instance
        log.info("plugin.load name=%s version=%s", name, version or "latest")
        return instance

    # ── Unload ──────────────────────────────────────────────────────────

    def unload(self, name: str) -> None:
        """Deactivate and remove a loaded plugin.

        Calls ``on_remove()`` before removal.

        Raises
        ------
        PluginError
            If plugin is not currently loaded.
        """
        instance = self._loaded.get(name)
        if instance is None:
            raise PluginError(f"Plugin {name!r} is not loaded")

        instance.on_remove()
        del self._loaded[name]
        log.info("plugin.unload name=%s", name)

    # ── List ────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[PluginMetadata]:
        """Return metadata for all available plugins (all versions, latest only).

        Returns the latest version metadata for each available plugin.
        """
        result: list[PluginMetadata] = []
        for _name, versions in self._available.items():
            latest_version = sorted(versions.keys())[-1]
            cls = versions[latest_version]
            result.append(cls.plugin_metadata)
        return result
