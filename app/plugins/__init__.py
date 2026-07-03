"""
Feed plugin system.

Import `plugin_registry` for dispatch, or `all_plugins` for the metadata list.
Plugins are registered in priority order — first match wins.
"""
from .base import FeedPlugin, ParsedArticle, ParsedFeed
from .registry import plugin_registry
from .youtube import YouTubePlugin
from .github import GitHubPlugin
from .feedly import FeedlyPlugin
from .podcast import PodcastPlugin
from .default import DefaultPlugin

# ── Registration order matters — specific plugins before the default fallback ──
# Discovery-only plugins (can_handle=False) are listed last so they appear in
# the /plugins list but never intercept feed fetching.
plugin_registry.register(YouTubePlugin())
plugin_registry.register(GitHubPlugin())
plugin_registry.register(DefaultPlugin())   # fetch fallback — must come before discovery-only
plugin_registry.register(FeedlyPlugin())    # discovery only
plugin_registry.register(PodcastPlugin())  # discovery only

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
