"""
Feed search and discovery router — core dispatcher only.

## Architecture

This router contains ZERO third-party API calls.
All source-specific logic lives in app/plugins/:

  YouTubePlugin  → search_sources=["youtube"],  discover()
  FeedlyPlugin   → search_sources=["feedly"],   discover()
  PodcastPlugin  → search_sources=["itunes", "podcast_index", "gpodder", "fyyd"]
  GitHubPlugin   → search_sources=["github"]

To add a new search index:
  1. Create (or extend) a plugin with SearchSourceMeta in search_sources
  2. Implement search(query, source_id, **kwargs)
  3. Register it in app/plugins/__init__.py
  4. Done — no changes to this file needed.

Endpoints:
  GET /search/indexes          → list all registered search sources
  GET /search/feeds?q&source   → delegate to plugin.search()
  GET /search/discover?url     → HTML scrape + plugin fast-paths
"""
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query

from ..plugins import plugin_registry
from ..schemas import DiscoveredFeed, FeedDiscoverResponse, FeedSearchResponse, FeedSearchResult
from ..services.url_safety import assert_public_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["Search"])

_HEADERS = {"User-Agent": "RSSReader/1.0"}

# ── Search index listing ──────────────────────────────────────────────────────

@router.get("/indexes", summary="List available feed search indexes")
def list_search_indexes():
    """Return metadata for all registered search sources, aggregated from plugins."""
    return [
        {
            "id":               s.id,
            "name":             s.name,
            "description":      s.description,
            "category":         s.category,
            "icon":             s.icon,
            "placeholder":      s.placeholder,
            "requires_key":     s.requires_key,
            "requires_key_hint": s.requires_key_hint,
        }
        for s in plugin_registry.list_search_sources()
    ]

# ── Feed search ───────────────────────────────────────────────────────────────

@router.get("/feeds", response_model=FeedSearchResponse, summary="Search feed indexes")
async def search_feeds(
    q: str = Query(..., min_length=1),
    source: str = Query("feedly", description="Search source id — see /search/indexes"),
    limit: int = Query(20, ge=1, le=100),
    locale: str = Query("en", description="Locale hint (Feedly only)"),
):
    plugin = plugin_registry.get_search_plugin(source)
    if not plugin:
        raise HTTPException(status_code=400, detail=f"Unknown search source: {source!r}. See /search/indexes.")

    results = await plugin.search(q, source_id=source, limit=limit, locale=locale)

    return FeedSearchResponse(
        query=q,
        results=[
            FeedSearchResult(
                feed_url    = r.feed_url,
                title       = r.title,
                description = r.description,
                website_url = r.website_url,
                subscribers = r.subscribers,
                language    = r.language,
                cover_url   = r.cover_url,
                velocity    = r.velocity,
            )
            for r in results
        ],
    )

# ── Website feed discovery ────────────────────────────────────────────────────
# Generic HTML <link rel="alternate"> scraping + common-path probing.
# Plugin fast-paths run first (e.g. YouTubePlugin.discover() for youtube.com URLs).

_FEED_MIME_TYPES = {
    "application/rss+xml":  "rss",
    "application/atom+xml": "atom",
    "application/feed+json": "json",
    "application/json":      "json",
}

_COMMON_PATHS = [
    "/feed", "/feed.xml", "/rss", "/rss.xml", "/atom.xml",
    "/feed/atom", "/feeds/posts/default", "/index.xml",
    "/?feed=rss2", "/?feed=atom",
]


@router.get("/discover", response_model=FeedDiscoverResponse, summary="Discover RSS/Atom feeds on a website")
async def discover_feeds(url: str = Query(..., description="Website URL to inspect")):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")
    try:
        assert_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Plugin fast-path: find the first plugin whose can_discover(url) is True.
    # Only ONE plugin is tried — the one that declared ownership of this domain.
    # This prevents every plugin from making HTTP calls for every URL.
    discover_plugin = plugin_registry.get_discover_plugin(url)
    if discover_plugin:
        try:
            found = await discover_plugin.discover(url)
            if found:
                return FeedDiscoverResponse(
                    source_url=url,
                    feeds=[DiscoveredFeed(feed_url=f.feed_url, title=f.title, feed_type=f.feed_type) for f in found],
                )
        except Exception as exc:
            logger.debug("Plugin %s discover() failed for %s: %s", discover_plugin.name, url, exc)

    # Generic HTML scraping — domain-agnostic core logic (no third-party calls)
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=_HEADERS) as client:
        try:
            page_resp = await client.get(url)
            page_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"Could not fetch URL: {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"URL unreachable: {exc}")

        found: list[DiscoveredFeed] = []
        seen: set[str] = set()

        if "html" in page_resp.headers.get("content-type", ""):
            soup = BeautifulSoup(page_resp.text, "html.parser")
            for link in soup.find_all("link", rel=lambda r: r and "alternate" in r):
                mime = (link.get("type") or "").lower().strip()
                feed_type = _FEED_MIME_TYPES.get(mime)
                if not feed_type:
                    continue
                href = link.get("href", "").strip()
                if not href:
                    continue
                feed_url = urljoin(str(page_resp.url), href)
                if feed_url not in seen:
                    seen.add(feed_url)
                    found.append(DiscoveredFeed(feed_url=feed_url, title=link.get("title"), feed_type=feed_type))

        if not found:
            base = f"{parsed.scheme}://{parsed.netloc}"
            for path in _COMMON_PATHS:
                result = await _probe_feed(client, base + path, seen)
                if result:
                    found.append(result)

    return FeedDiscoverResponse(source_url=url, feeds=found)


async def _probe_feed(client: httpx.AsyncClient, url: str, seen: set[str]) -> DiscoveredFeed | None:
    if url in seen:
        return None
    try:
        resp = await client.head(url, timeout=5.0)
        if resp.status_code not in (200, 301, 302, 307, 308):
            return None
        content_type = resp.headers.get("content-type", "")
    except httpx.RequestError:
        return None

    for mime, feed_type in _FEED_MIME_TYPES.items():
        if mime in content_type:
            seen.add(url)
            return DiscoveredFeed(feed_url=url, feed_type=feed_type)

    try:
        resp = await client.get(url, timeout=5.0,
                                headers={"Accept": "application/rss+xml,application/atom+xml,text/xml,*/*"})
        if resp.status_code != 200:
            return None
        body = resp.text[:512]
    except httpx.RequestError:
        return None

    feed_type = _sniff_feed_type(body)
    if feed_type:
        seen.add(url)
        return DiscoveredFeed(feed_url=url, feed_type=feed_type)
    return None


def _sniff_feed_type(body: str) -> str | None:
    import re
    s = body.lstrip()
    if re.search(r"<rss\b", s, re.IGNORECASE):
        return "rss"
    if re.search(r"<feed\b", s, re.IGNORECASE):
        return "atom"
    if s.startswith("{") and '"version"' in s and "jsonfeed" in s.lower():
        return "json"
    return None


# ── Backward-compat helper (used by feeds router for YouTube URL normalisation) ─

async def _resolve_youtube_url(url: str) -> str | None:
    """Resolve a YouTube URL to its RSS feed. Delegates to YouTubePlugin.resolve_url()."""
    from ..plugins.youtube import YouTubePlugin
    return await YouTubePlugin().resolve_url(url)
