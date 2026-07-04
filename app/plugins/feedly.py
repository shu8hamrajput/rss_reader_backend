"""
Feedly plugin — general RSS feed discovery.

fetch():  not implemented (Feedly-discovered feeds are plain RSS/Atom,
          handled by DefaultPlugin or YouTubePlugin based on URL).
search(): searches Feedly's public index of 40M+ RSS feeds. No API key required.
discover(): finds feeds for a given website via Feedly's search API.
"""
from __future__ import annotations

import logging

import httpx

from .base import DiscoveredFeed, DiscoveryPlugin, SearchResult, SearchSourceMeta

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "RSSReader/1.0", "Accept": "application/json"}


class FeedlyPlugin(DiscoveryPlugin):
    name         = "feedly"
    display_name = "Feedly"
    description  = "Search Feedly's public index of 40M+ RSS feeds — no API key required"
    icon_emoji   = "🔍"

    search_sources = [
        SearchSourceMeta(
            id          = "feedly",
            name        = "Feedly",
            description = "40M+ RSS feeds — blogs, newsletters, news",
            category    = "general",
            icon        = "🔍",
            placeholder = "e.g. python, tech news, startup",
            requires_key = False,
        ),
    ]

    async def search(self, query: str, source_id: str, limit: int = 20, locale: str = "en", **kwargs) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    "https://cloud.feedly.com/v3/search/feeds",
                    params={"query": query, "count": limit, "locale": locale},
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Feedly search HTTP %s for %r", exc.response.status_code, query)
            return []
        except httpx.RequestError as exc:
            logger.warning("Feedly search unreachable: %s", exc)
            return []

        data = resp.json()
        results = []
        for item in data.get("results", []):
            feed_id = item.get("feedId", "")
            feed_url = feed_id.removeprefix("feed/") if feed_id.startswith("feed/") else feed_id
            if not feed_url:
                continue
            results.append(SearchResult(
                feed_url    = feed_url,
                title       = item.get("title"),
                description = item.get("description"),
                website_url = item.get("website"),
                subscribers = item.get("subscribers"),
                language    = item.get("language"),
                cover_url   = item.get("coverUrl"),
                velocity    = item.get("velocity"),
            ))
        return results

    async def discover(self, url: str) -> list[DiscoveredFeed]:
        results = await self.search(f"site:{url}", source_id="feedly", limit=10)
        return [DiscoveredFeed(feed_url=r.feed_url, title=r.title) for r in results]
