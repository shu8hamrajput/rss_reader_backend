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
    from .base import FeedPlugin, SearchSourceMeta

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: list[FeedPlugin] = []
        self._search_index: dict[str, FeedPlugin] = {}  # source_id → plugin

    def register(self, plugin: FeedPlugin) -> None:
        self._plugins.append(plugin)
        for src in plugin.search_sources:
            if src.id in self._search_index:
                logger.warning(
                    "Search source %r already registered by %r; overriding with %r",
                    src.id, self._search_index[src.id].name, plugin.name,
                )
            self._search_index[src.id] = plugin
        logger.debug("Registered feed plugin: %s", plugin.name)

    # ── Fetch dispatch ────────────────────────────────────────────────────────

    def get_fetch_plugin(self, url: str) -> FeedPlugin:
        """Return the first plugin that can handle this feed URL for fetching."""
        for p in self._plugins:
            if p.can_handle(url):
                return p
        raise RuntimeError(f"No plugin can handle URL: {url!r}")

    # Keep old name as alias for backward compat with feed_parser.py
    def get_plugin(self, url: str) -> FeedPlugin:
        return self.get_fetch_plugin(url)

    # ── Search dispatch ───────────────────────────────────────────────────────

    def get_search_plugin(self, source_id: str) -> FeedPlugin | None:
        """Return the plugin that owns the given search source, or None."""
        return self._search_index.get(source_id)

    def list_search_sources(self) -> list[SearchSourceMeta]:
        """Return all search source metadata from all plugins, in registration order."""
        seen: set[str] = set()
        result = []
        for plugin in self._plugins:
            for src in plugin.search_sources:
                if src.id not in seen:
                    seen.add(src.id)
                    result.append(src)
        return result

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def all_plugins(self) -> list[FeedPlugin]:
        return list(self._plugins)


# Module-level singleton
plugin_registry = PluginRegistry()
