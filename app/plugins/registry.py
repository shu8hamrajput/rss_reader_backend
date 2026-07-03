"""
Plugin registry — singleton that maps feed URLs to their handler plugin.

Plugins are tried in registration order; the first match wins.
The default plugin (registered last) matches everything as a fallback.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import FeedPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: list[FeedPlugin] = []

    def register(self, plugin: FeedPlugin) -> None:
        self._plugins.append(plugin)
        logger.debug("Registered feed plugin: %s", plugin.name)

    def get_plugin(self, url: str) -> FeedPlugin:
        """Return the first plugin that can handle `url`."""
        for p in self._plugins:
            if p.can_handle(url):
                return p
        # Should never happen because DefaultPlugin matches everything, but be safe
        raise RuntimeError(f"No plugin can handle URL: {url!r}")

    @property
    def all_plugins(self) -> list[FeedPlugin]:
        return list(self._plugins)


# Module-level singleton — import this everywhere
plugin_registry = PluginRegistry()
