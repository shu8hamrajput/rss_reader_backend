"""
Feed plugin protocol.

## Architecture rule

The core backend handles:
  - HTTP routing, auth, rate-limiting
  - Database read/write (Feed, Article, User, …)
  - Task scheduling (Celery beat/worker)
  - OPML, webhooks, collections, highlights

Plugins handle EVERYTHING type-specific:
  - How to fetch and parse a feed URL       → fetch()
  - How to convert a user URL to a feed URL  → normalize_url()
  - What search sources the plugin exposes   → search_sources
  - How to search those sources              → search()
  - How to discover feeds on a website       → discover()

Adding a new feed type = one new file in app/plugins/, one register() call.
The router never contains HTTP calls to third-party APIs. Ever.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


# ── Data transfer objects ─────────────────────────────────────────────────────

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


@dataclass
class SearchResult:
    """Normalised result from any search source."""
    feed_url: str
    title: str | None = None
    description: str | None = None
    website_url: str | None = None
    cover_url: str | None = None
    subscribers: int | None = None
    language: str | None = None
    velocity: float | None = None


@dataclass
class DiscoveredFeed:
    feed_url: str
    title: str | None = None
    feed_type: str | None = None   # "rss" | "atom" | "json"


@dataclass
class SearchSourceMeta:
    """Metadata for one search source exposed by a plugin.

    A single plugin can expose multiple sources (e.g. PodcastPlugin exposes
    itunes, podcast_index, gpodder, fyyd).  The id is what the frontend sends
    as ?source=... and what the registry indexes on.
    """
    id: str
    name: str
    description: str
    category: str          # "general" | "video" | "podcast" | "dev"
    icon: str
    placeholder: str
    requires_key: bool = False
    requires_key_hint: str | None = None


# ── Plugin base class ─────────────────────────────────────────────────────────

class FeedPlugin(ABC):
    """
    Base class for all feed plugins.

    ## Subclass contract

    Required:
        name          class-level str slug, unique across plugins ("youtube")
        display_name  human label ("YouTube")
        can_handle()  return True if this plugin owns the given feed URL
        fetch()       fetch + parse the URL → ParsedFeed

    Optional (override to enable):
        search_sources     list of SearchSourceMeta this plugin exposes for discovery
        normalize_url()    convert user input to a canonical feed URL
        search()           search a specific source_id
        discover()         find feeds on a website URL
    """

    # ── Plugin identity ───────────────────────────────────────────────────────
    name: str          # slug, stored in Feed.plugin_name
    display_name: str
    description: str = ""
    icon_emoji: str = "📡"

    # ── Search / discovery metadata ────────────────────────────────────────────
    # Override in subclasses that expose search sources.
    search_sources: list[SearchSourceMeta] = []

    # ── Required ──────────────────────────────────────────────────────────────

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this plugin should own the given feed URL for fetching."""

    @abstractmethod
    async def fetch(
        self,
        url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[ParsedFeed | None, int]:
        """Fetch and parse the feed.

        Returns (ParsedFeed, http_status_code).
        Return (None, 304) on Not Modified.
        Raise on errors.
        """

    # ── Optional ──────────────────────────────────────────────────────────────

    def normalize_url(self, url: str) -> str:
        """Convert user input to the canonical feed URL.

        E.g. youtube.com/@handle → feeds/videos.xml?channel_id=UC…
             github.com/owner/repo → github.com/owner/repo/releases.atom
        """
        return url

    async def search(
        self,
        query: str,
        source_id: str,
        limit: int = 20,
        **kwargs,
    ) -> list[SearchResult]:
        """Search the given source_id for feeds matching query.

        Only called when source_id is in [s.id for s in self.search_sources].
        Return [] by default (plugin doesn't search).
        """
        return []

    async def discover(self, url: str) -> list[DiscoveredFeed]:
        """Discover feeds on a website URL.

        Called by the /search/discover endpoint when the URL matches
        this plugin's domain. Return [] to fall through to generic HTML scraping.
        """
        return []

    def __repr__(self) -> str:
        return f"<FeedPlugin {self.name!r}>"
