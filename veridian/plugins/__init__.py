"""
veridian.plugins
────────────────
Plugin SDK, registry, certification, and marketplace for Veridian.

Third-party developers use this package to build, publish, and discover
plugins that extend Veridian with custom verifiers and hooks.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CertificationResult",
    "CertificationSuite",
    "HookPlugin",
    "MarketplaceEntry",
    "MarketplaceIndex",
    "PluginMetadata",
    "PluginRegistry",
    "VerifierPlugin",
    "plugin_manifest",
]

_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "CertificationResult": ("veridian.plugins.certification", "CertificationResult"),
    "CertificationSuite": ("veridian.plugins.certification", "CertificationSuite"),
    "HookPlugin": ("veridian.plugins.sdk", "HookPlugin"),
    "MarketplaceEntry": ("veridian.plugins.marketplace", "MarketplaceEntry"),
    "MarketplaceIndex": ("veridian.plugins.marketplace", "MarketplaceIndex"),
    "PluginMetadata": ("veridian.plugins.sdk", "PluginMetadata"),
    "PluginRegistry": ("veridian.plugins.registry", "PluginRegistry"),
    "VerifierPlugin": ("veridian.plugins.sdk", "VerifierPlugin"),
    "plugin_manifest": ("veridian.plugins.sdk", "plugin_manifest"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORT_MAP:
        raise AttributeError(name)
    module_name, symbol = _EXPORT_MAP[name]
    module = __import__(module_name, fromlist=[symbol])
    value = getattr(module, symbol)
    globals()[name] = value
    return value
