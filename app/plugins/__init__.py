"""
Feed plugin system.

Import `plugin_registry` for dispatch, or `all_plugins` for the metadata list.
Plugins are registered in priority order — first match wins.
"""
from .base import FeedPlugin, ParsedArticle, ParsedFeed
from .registry import plugin_registry
from .youtube import YouTubePlugin
from .github import GitHubPlugin
from .default import DefaultPlugin

# ── Registration order matters — specific plugins before the default fallback ──
plugin_registry.register(YouTubePlugin())
plugin_registry.register(GitHubPlugin())
plugin_registry.register(DefaultPlugin())   # must be last

__all__ = [
    "FeedPlugin",
    "ParsedArticle",
    "ParsedFeed",
    "plugin_registry",
    "YouTubePlugin",
    "GitHubPlugin",
    "DefaultPlugin",
]
