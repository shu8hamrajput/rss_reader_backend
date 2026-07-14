"""
Plugin registry — dispatches feed URLs to fetch plugins and search queries
to search plugins.

Two dispatch axes:
  get_fetch_plugin(url)       → first plugin where can_handle(url) is True
  get_search_plugin(source_id) → plugin that owns the given search source id

All search source metadata is also aggregated here for the /search/indexes endpoint.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import DiscoveryPlugin, FeedPlugin, SearchSourceMeta
# Runtime imports for isinstance checks and typed return annotations
from .base import DiscoveryPlugin as _DiscoveryPlugin, FeedPlugin as _FeedPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._fetch_plugins: list[FeedPlugin] = []          # ordered, can_handle dispatch
        self._discovery_plugins: list[DiscoveryPlugin] = [] # discovery-only
        self._search_index: dict[str, object] = {}           # source_id → FeedPlugin | DiscoveryPlugin

    def register(self, plugin) -> None:
        """Register a FeedPlugin (fetch+optional search) or DiscoveryPlugin (search only)."""
        if isinstance(plugin, _FeedPlugin):
            self._fetch_plugins.append(plugin)
            kind = "fetch"
        elif isinstance(plugin, _DiscoveryPlugin):
            self._discovery_plugins.append(plugin)
            kind = "discovery"
        else:
            raise TypeError(f"Expected FeedPlugin or DiscoveryPlugin, got {type(plugin).__name__}")

        for src in getattr(plugin, "search_sources", []):
            if src.id in self._search_index:
                logger.warning("Search source %r already registered; overriding", src.id)
            self._search_index[src.id] = plugin
        logger.debug("Registered %s plugin: %s", kind, plugin.name)

    # ── Fetch dispatch ────────────────────────────────────────────────────────

    def get_fetch_plugin(self, url: str) -> FeedPlugin:
        """Return the first FeedPlugin that can handle this URL."""
        for p in self._fetch_plugins:
            if p.can_handle(url):
                return p
        raise RuntimeError(f"No plugin can handle URL: {url!r}")

    # Backward compat alias
    def get_plugin(self, url: str) -> FeedPlugin:
        return self.get_fetch_plugin(url)

    # ── Search dispatch ───────────────────────────────────────────────────────

    def get_search_plugin(self, source_id: str):
        """Return the plugin owning this search source, or None."""
        return self._search_index.get(source_id)

    def list_search_sources(self) -> list[SearchSourceMeta]:
        seen: set[str] = set()
        result = []
        for plugin in [*self._fetch_plugins, *self._discovery_plugins]:
            for src in getattr(plugin, "search_sources", []):
                if src.id not in seen:
                    seen.add(src.id)
                    result.append(src)
        return result

    # ── Discovery dispatch ────────────────────────────────────────────────────

    def get_discover_plugin(self, url: str):
        """Return the first plugin whose can_discover(url) is True, or None."""
        for p in [*self._fetch_plugins, *self._discovery_plugins]:
            if p.can_discover(url):
                return p
        return None

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def fetch_plugins(self) -> list[_FeedPlugin]:
        """All registered FeedPlugins (can fetch feed URLs)."""
        return list(self._fetch_plugins)

    @property
    def discovery_plugins(self) -> list[_DiscoveryPlugin]:
        """All registered DiscoveryPlugins (search/discover only)."""
        return list(self._discovery_plugins)

    @property
    def all_plugins(self) -> list:
        """All plugins regardless of type. Prefer fetch_plugins or discovery_plugins when type matters."""
        return [*self._fetch_plugins, *self._discovery_plugins]


# Module-level singleton
plugin_registry = PluginRegistry()
