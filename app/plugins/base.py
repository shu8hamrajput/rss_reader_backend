"""
Feed plugin protocol.

Each plugin handles a specific family of feeds (YouTube, GitHub, podcasts, etc.).
Plugins are pure data-transformers: they fetch a URL and return ParsedFeed/ParsedArticle
structs. The database write layer in feed_parser.py handles persistence for all plugins.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedArticle:
    guid: str
    title: str | None = None
    url: str | None = None
    author: str | None = None
    summary: str | None = None
    content: str | None = None
    full_content: str | None = None      # transcript / pre-fetched HTML
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    media_type: str | None = None
    media_url: str | None = None
    duration_seconds: int | None = None
    episode_number: str | None = None
    itunes_author: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ParsedFeed:
    title: str | None = None
    description: str | None = None
    site_url: str | None = None
    icon_url: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    articles: list[ParsedArticle] = field(default_factory=list)


class FeedPlugin(ABC):
    """Base class for all feed plugins.

    Subclasses must set class-level attributes `name`, `display_name` and
    implement `can_handle()` and `fetch()`.
    """

    # ── Metadata (set on subclasses) ──────────────────────────────────────────
    name: str          # slug used in DB: "youtube", "github", "podcast", "default"
    display_name: str  # shown in UI: "YouTube", "GitHub Releases"
    description: str = ""
    icon_emoji: str = "📡"

    # ── Required interface ────────────────────────────────────────────────────

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this plugin should handle the given feed URL."""

    @abstractmethod
    async def fetch(
        self,
        url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[ParsedFeed | None, int]:
        """Fetch and parse the feed URL.

        Returns (ParsedFeed, http_status_code).
        Return (None, 304) when the feed is unchanged (ETag / Last-Modified hit).
        Raise on unrecoverable errors (non-2xx, parse failure, etc.).
        """

    # ── Optional extension points ─────────────────────────────────────────────

    def normalize_url(self, url: str) -> str:
        """Convert a user-provided URL to the canonical feed URL.

        Example: a YouTube channel page URL → the channel's RSS feed URL.
        Default implementation returns the URL unchanged.
        """
        return url

    async def search(self, query: str, **kwargs) -> list[dict]:
        """Search for feeds of this type. Returns list of FeedSearchResult-like dicts."""
        return []

    async def discover(self, url: str) -> list[dict]:
        """Discover feeds from a website URL. Returns list of DiscoveredFeed-like dicts."""
        return []

    def __repr__(self) -> str:
        return f"<FeedPlugin {self.name!r}>"
