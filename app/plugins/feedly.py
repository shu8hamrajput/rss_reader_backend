"""
Feedly discovery plugin.

Feedly doesn't have its own feed format — it indexes regular RSS/Atom feeds.
This plugin contributes:
  - search()   — searches Feedly's public feed index (no API key required)
  - discover() — finds feeds for a website URL via Feedly's endpoint

can_handle() returns False: Feedly-discovered feeds are parsed by whichever
plugin matches their actual URL (YouTubePlugin, DefaultPlugin, etc.).
"""
from __future__ import annotations

import logging

import httpx

from .base import FeedPlugin, ParsedFeed

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "RSSReader/1.0", "Accept": "application/json"}
_SEARCH_URL  = "https://cloud.feedly.com/v3/search/feeds"
_STREAM_URL  = "https://cloud.feedly.com/v3/streams/contents"   # for future use


class FeedlyPlugin(FeedPlugin):
    name         = "feedly"
    display_name = "Feedly"
    description  = "Search Feedly's public index of 40M+ RSS feeds — no API key required"
    icon_emoji   = "🔍"

    def can_handle(self, url: str) -> bool:
        # Feedly-discovered feeds are standard RSS/Atom; let other plugins handle them.
        return False

    async def fetch(
        self,
        url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[ParsedFeed | None, int]:
        raise NotImplementedError("FeedlyPlugin does not fetch feeds directly")

    async def search(self, query: str, limit: int = 20, locale: str = "en", **kwargs) -> list[dict]:
        """Search Feedly's public feed index.

        Returns a list of dicts matching the FeedSearchResult schema.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params={"query": query, "count": limit, "locale": locale},
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Feedly search failed: HTTP %s", exc.response.status_code)
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
            results.append({
                "feed_url":    feed_url,
                "title":       item.get("title"),
                "description": item.get("description"),
                "website_url": item.get("website"),
                "subscribers": item.get("subscribers"),
                "language":    item.get("language"),
                "cover_url":   item.get("coverUrl"),
                "velocity":    item.get("velocity"),
                "source":      "feedly",
            })
        return results

    async def discover(self, url: str, **kwargs) -> list[dict]:
        """Find feeds for a given website URL via Feedly's search API."""
        return await self.search(f"site:{url}", limit=10)
