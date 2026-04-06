"""
veridian.plugins.marketplace
────────────────────────────
In-memory marketplace index for plugin discovery and listing.
"""

from __future__ import annotations

from dataclasses import dataclass

from veridian.plugins.sdk import PluginMetadata

__all__ = ["MarketplaceEntry", "MarketplaceIndex"]


@dataclass(frozen=True)
class MarketplaceEntry:
    """Published metadata for a plugin build."""

    metadata: PluginMetadata
    certification_status: str
    trust_score: float


class MarketplaceIndex:
    """Simple in-memory plugin marketplace index."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], MarketplaceEntry] = {}

    def publish(self, entry: MarketplaceEntry) -> None:
        key = (entry.metadata.name, entry.metadata.version)
        self._entries[key] = entry

    def search(self, query: str) -> list[MarketplaceEntry]:
        needle = query.lower().strip()
        if not needle:
            return self.list_all()
        return [
            entry
            for entry in self._entries.values()
            if needle in entry.metadata.name.lower() or needle in entry.metadata.description.lower()
        ]

    def list_all(self) -> list[MarketplaceEntry]:
        return list(self._entries.values())
