"""
Feed search and discovery.

- GET /search/feeds?q=...&source=feedly|podcast_index|itunes|gpodder|fyyd
- GET /search/discover?url=...   → scrape a website and return its RSS/Atom links

Supported search indexes:
  feedly        (default) Feedly public index — blogs, newsletters, general RSS. No key.
  podcast_index Podcast Index (podcastindex.org) — best open podcast DB. Free key required:
                  set PODCAST_INDEX_KEY + PODCAST_INDEX_SECRET in env.
  itunes        Apple Podcasts / iTunes — mainstream podcasts. No key.
  gpodder       gpodder.net — community podcast directory. No key.
  fyyd          fyyd.de — European podcast directory. No key.
"""
import hashlib
import os
import re
import time
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query

from ..schemas import (
    DiscoveredFeed,
    FeedDiscoverResponse,
    FeedSearchResponse,
    FeedSearchResult,
)

router = APIRouter(prefix="/search", tags=["Search"])

_HEADERS = {"User-Agent": "RSSReader/1.0 (+https://github.com)"}

SearchSource = Literal["feedly", "podcast_index", "itunes", "gpodder", "fyyd"]

# ── Per-source fetch helpers ──────────────────────────────────────────────────

async def _search_feedly(q: str, limit: int, locale: str) -> FeedSearchResponse:
    url = "https://cloud.feedly.com/v3/search/feeds"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"query": q, "count": limit, "locale": locale}, headers=_HEADERS)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Feedly error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Feedly unreachable: {exc}")

    data = resp.json()
    results: list[FeedSearchResult] = []
    for item in data.get("results", []):
        feed_id: str = item.get("feedId", "")
        feed_url = feed_id.removeprefix("feed/") if feed_id.startswith("feed/") else feed_id
        if not feed_url:
            continue
        results.append(FeedSearchResult(
            feed_url=feed_url,
            title=item.get("title"),
            description=item.get("description"),
            website_url=item.get("website"),
            subscribers=item.get("subscribers"),
            language=item.get("language"),
            cover_url=item.get("coverUrl"),
            velocity=item.get("velocity"),
        ))
    return FeedSearchResponse(query=q, results=results, related_queries=data.get("related", []))


async def _search_podcast_index(q: str, limit: int) -> FeedSearchResponse:
    api_key = os.getenv("PODCAST_INDEX_KEY", "")
    api_secret = os.getenv("PODCAST_INDEX_SECRET", "")
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="Podcast Index requires PODCAST_INDEX_KEY and PODCAST_INDEX_SECRET env vars. "
                   "Register free at podcastindex.org/apps",
        )
    ts = str(int(time.time()))
    auth_hash = hashlib.sha256(f"{api_key}{api_secret}{ts}".encode()).hexdigest()
    headers = {
        **_HEADERS,
        "X-Auth-Key": api_key,
        "X-Auth-Date": ts,
        "Authorization": auth_hash,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.podcastindex.org/api/1.0/search/byterm",
                params={"q": q, "max": limit},
                headers=headers,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Podcast Index error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Podcast Index unreachable: {exc}")

    data = resp.json()
    results = [
        FeedSearchResult(
            feed_url=item.get("url", ""),
            title=item.get("title"),
            description=item.get("description"),
            website_url=item.get("link"),
            subscribers=item.get("episodeCount"),
            language=item.get("language"),
            cover_url=item.get("image") or item.get("artwork"),
            velocity=None,
        )
        for item in data.get("feeds", [])
        if item.get("url")
    ]
    return FeedSearchResponse(query=q, results=results)


async def _search_itunes(q: str, limit: int) -> FeedSearchResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"media": "podcast", "entity": "podcast", "term": q, "limit": limit},
                headers=_HEADERS,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"iTunes error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"iTunes unreachable: {exc}")

    data = resp.json()
    results = [
        FeedSearchResult(
            feed_url=item.get("feedUrl", ""),
            title=item.get("collectionName"),
            description=item.get("artistName"),
            website_url=item.get("collectionViewUrl"),
            subscribers=item.get("trackCount"),
            language=item.get("primaryGenreName"),
            cover_url=item.get("artworkUrl600") or item.get("artworkUrl100"),
            velocity=None,
        )
        for item in data.get("results", [])
        if item.get("feedUrl")
    ]
    return FeedSearchResponse(query=q, results=results)


async def _search_gpodder(q: str, limit: int) -> FeedSearchResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://gpodder.net/search.json",
                params={"q": q},
                headers=_HEADERS,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"gpodder error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"gpodder unreachable: {exc}")

    items = resp.json()
    if not isinstance(items, list):
        items = []
    results = [
        FeedSearchResult(
            feed_url=item.get("url", ""),
            title=item.get("title"),
            description=item.get("description"),
            website_url=item.get("website"),
            subscribers=item.get("subscribers"),
            language=None,
            cover_url=item.get("logo_url") or item.get("scaled_logo_url"),
            velocity=None,
        )
        for item in items[:limit]
        if item.get("url")
    ]
    return FeedSearchResponse(query=q, results=results)


async def _search_fyyd(q: str, limit: int) -> FeedSearchResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.fyyd.de/0.2/search/podcast",
                params={"term": q, "count": limit},
                headers=_HEADERS,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"fyyd error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"fyyd unreachable: {exc}")

    data = resp.json()
    items = data.get("data", [])
    if not isinstance(items, list):
        items = []
    results = [
        FeedSearchResult(
            feed_url=item.get("xmlURL", ""),
            title=item.get("title"),
            description=item.get("description"),
            website_url=item.get("htmlURL"),
            subscribers=item.get("episode_count"),
            language=item.get("language"),
            cover_url=item.get("layoutImageURL") or item.get("smallImageURL"),
            velocity=None,
        )
        for item in items
        if item.get("xmlURL")
    ]
    return FeedSearchResponse(query=q, results=results)


# ── Feed search endpoint ──────────────────────────────────────────────────────

@router.get(
    "/feeds",
    response_model=FeedSearchResponse,
    summary="Search feed indexes",
    description=(
        "Search for RSS/Atom/podcast feeds using the selected index. "
        "`source` defaults to `feedly` (general RSS). "
        "Use `podcast_index`, `itunes`, `gpodder`, or `fyyd` for podcast-focused results."
    ),
)
async def search_feeds(
    q: str = Query(..., min_length=1),
    source: SearchSource = Query("feedly", description="Which index to query"),
    limit: int = Query(20, ge=1, le=100),
    locale: str = Query("en", description="Locale hint (Feedly only)"),
):
    if source == "feedly":
        return await _search_feedly(q, limit, locale)
    if source == "podcast_index":
        return await _search_podcast_index(q, limit)
    if source == "itunes":
        return await _search_itunes(q, limit)
    if source == "gpodder":
        return await _search_gpodder(q, limit)
    if source == "fyyd":
        return await _search_fyyd(q, limit)


# ── Website feed discovery ────────────────────────────────────────────────────

# Mime types that indicate RSS/Atom/JSON feed links in <link> tags
_FEED_MIME_TYPES = {
    "application/rss+xml": "rss",
    "application/atom+xml": "atom",
    "application/feed+json": "json",
    "application/json": "json",
}

# Common feed path suffixes to probe when no <link> tags are found
_COMMON_PATHS = [
    "/feed",
    "/feed.xml",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/feed/atom",
    "/feeds/posts/default",
    "/index.xml",
    "/?feed=rss2",
    "/?feed=atom",
]


@router.get(
    "/discover",
    response_model=FeedDiscoverResponse,
    summary="Discover RSS/Atom feeds on a website",
)
async def discover_feeds(
    url: str = Query(..., description="URL of the website to inspect"),
):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=_HEADERS) as client:
        try:
            page_resp = await client.get(url)
            page_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"Could not fetch URL: {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"URL unreachable: {exc}")

        found: list[DiscoveredFeed] = []
        seen_urls: set[str] = set()

        content_type = page_resp.headers.get("content-type", "")
        if "html" in content_type or not content_type:
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
                if feed_url not in seen_urls:
                    seen_urls.add(feed_url)
                    found.append(DiscoveredFeed(feed_url=feed_url, title=link.get("title"), feed_type=feed_type))

        if not found:
            base = f"{parsed.scheme}://{parsed.netloc}"
            probe_tasks = [_probe_feed(client, base + path, seen_urls) for path in _COMMON_PATHS]
            for task in probe_tasks:
                result = await task
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
            return DiscoveredFeed(feed_url=url, title=None, feed_type=feed_type)

    try:
        resp = await client.get(
            url, timeout=5.0,
            headers={"Accept": "application/rss+xml,application/atom+xml,text/xml,*/*"},
        )
        if resp.status_code != 200:
            return None
        body = resp.text[:512]
    except httpx.RequestError:
        return None

    feed_type = _sniff_feed_type(body)
    if feed_type:
        seen.add(url)
        return DiscoveredFeed(feed_url=url, title=None, feed_type=feed_type)
    return None


def _sniff_feed_type(body: str) -> str | None:
    snippet = body.lstrip()
    if re.search(r"<rss\b", snippet, re.IGNORECASE):
        return "rss"
    if re.search(r"<feed\b", snippet, re.IGNORECASE):
        return "atom"
    if snippet.startswith("{") and '"version"' in snippet and "jsonfeed" in snippet.lower():
        return "json"
    return None
