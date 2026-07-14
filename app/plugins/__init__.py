"""
Feed plugin system. See ARCHITECTURE.md.

Two plugin types:
  FeedPlugin      — fetches and parses feed URLs (register these BEFORE DefaultPlugin)
  DiscoveryPlugin — search/discovery only, never intercepts fetching

Registration order matters for FeedPlugin (first can_handle() match wins).
DiscoveryPlugin order is irrelevant for fetching but affects search_sources ordering.
"""
from .base import DiscoveryPlugin, FeedPlugin, ParsedArticle, ParsedFeed
from .registry import plugin_registry
from .youtube import YouTubePlugin
from .github import GitHubPlugin
from .feedly import FeedlyPlugin
from .podcast import PodcastPlugin
from .default import DefaultPlugin

# FeedPlugins: specific before fallback
plugin_registry.register(YouTubePlugin())
plugin_registry.register(GitHubPlugin())
plugin_registry.register(DefaultPlugin())  # must be last — matches everything

# DiscoveryPlugins: search/discover only, never intercept fetching
plugin_registry.register(FeedlyPlugin())
plugin_registry.register(PodcastPlugin())

__all__ = [
    "FeedPlugin",
    "ParsedArticle",
    "ParsedFeed",
    "plugin_registry",
    "YouTubePlugin",
    "GitHubPlugin",
    "FeedlyPlugin",
    "PodcastPlugin",
    "DefaultPlugin",
]
