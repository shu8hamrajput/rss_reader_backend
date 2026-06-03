"""
Feed search and discovery.

- GET /search/feeds?q=...        → query Feedly's public feed index
- GET /search/discover?url=...   → scrape a website and return its RSS/Atom links
"""
import re
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

FEEDLY_SEARCH_URL = "https://cloud.feedly.com/v3/search/feeds"

_HEADERS = {"User-Agent": "RSSReader/1.0 (+https://github.com)"}

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


# ── Feedly index search ───────────────────────────────────────────────────────

@router.get(
    "/feeds",
    response_model=FeedSearchResponse,
    summary="Search the Feedly public feed index",
    description=(
        "Queries Feedly's public feed-search API to find RSS/Atom feeds matching "
        "the given keyword. No API key required. Returns up to `limit` results "
        "ordered by subscriber count."
    ),
)
async def search_feeds(
    q: str = Query(..., min_length=1, description="Search query, e.g. 'python', 'tech news'"),
    limit: int = Query(20, ge=1, le=100, description="Max results to return"),
    locale: str = Query("en", description="Preferred language locale, e.g. 'en', 'de'"),
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                FEEDLY_SEARCH_URL,
                params={"query": q, "count": limit, "locale": locale},
                headers=_HEADERS,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Feedly search error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Feedly unreachable: {exc}")

    data = resp.json()
    results: list[FeedSearchResult] = []
    for item in data.get("results", []):
        feed_id: str = item.get("feedId", "")
        # feedId format is "feed/https://..." — strip the prefix
        feed_url = feed_id.removeprefix("feed/") if feed_id.startswith("feed/") else feed_id
        if not feed_url:
            continue
        results.append(
            FeedSearchResult(
                feed_url=feed_url,
                title=item.get("title"),
                description=item.get("description"),
                website_url=item.get("website"),
                subscribers=item.get("subscribers"),
                language=item.get("language"),
                cover_url=item.get("coverUrl"),
                velocity=item.get("velocity"),
            )
        )

    return FeedSearchResponse(
        query=q,
        results=results,
        related_queries=data.get("related", []),
    )


# ── Website feed discovery ────────────────────────────────────────────────────

@router.get(
    "/discover",
    response_model=FeedDiscoverResponse,
    summary="Discover RSS/Atom feeds on a website",
    description=(
        "Fetches the given URL, parses `<link rel='alternate'>` tags for RSS/Atom "
        "references, and probes common feed paths as a fallback. Returns every feed "
        "found on that domain."
    ),
)
async def discover_feeds(
    url: str = Query(..., description="URL of the website to inspect, e.g. 'https://example.com'"),
):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=15.0, headers=_HEADERS
    ) as client:
        # 1. Fetch the page and look for <link rel="alternate"> tags
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
                    found.append(
                        DiscoveredFeed(
                            feed_url=feed_url,
                            title=link.get("title"),
                            feed_type=feed_type,
                        )
                    )

        # 2. Probe common paths if nothing was found in markup
        if not found:
            base = f"{parsed.scheme}://{parsed.netloc}"
            probe_tasks = [_probe_feed(client, base + path, seen_urls) for path in _COMMON_PATHS]
            for task in probe_tasks:
                result = await task
                if result:
                    found.append(result)

    return FeedDiscoverResponse(source_url=url, feeds=found)


async def _probe_feed(
    client: httpx.AsyncClient,
    url: str,
    seen: set[str],
) -> DiscoveredFeed | None:
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

    # HEAD might not return the right content-type; do a quick GET with Accept header
    try:
        resp = await client.get(
            url,
            timeout=5.0,
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
