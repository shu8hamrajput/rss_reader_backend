"""
Podcast discovery plugin.

Exposes four search sources — iTunes, Podcast Index, gpodder, fyyd.
Fetching is handled by DefaultPlugin (podcast feeds are standard RSS+iTunes namespace).
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import httpx

from .base import DiscoveryPlugin, SearchResult, SearchSourceMeta

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "RSSReader/1.0", "Accept": "application/json"}


class PodcastPlugin(DiscoveryPlugin):
    name         = "podcast"
    display_name = "Podcasts"
    description  = "Search Apple Podcasts, Podcast Index, gpodder, and fyyd"
    icon_emoji   = "🎙️"

    search_sources = [
        SearchSourceMeta(
            id          = "itunes",
            name        = "Apple Podcasts",
            description = "Apple's podcast catalog — largest index, no key needed",
            category    = "podcast",
            icon        = "🎙️",
            placeholder = "e.g. Software Engineering Daily",
        ),
        SearchSourceMeta(
            id               = "podcast_index",
            name             = "Podcast Index",
            description      = "Open, censorship-resistant podcast database",
            category         = "podcast",
            icon             = "📡",
            placeholder      = "e.g. indie podcast, tech",
            requires_key     = True,
            requires_key_hint= "Free key at podcastindex.org/apps",
        ),
        SearchSourceMeta(
            id          = "gpodder",
            name        = "gpodder",
            description = "Community podcast directory — no key needed",
            category    = "podcast",
            icon        = "🎧",
            placeholder = "e.g. linux, security",
        ),
        SearchSourceMeta(
            id          = "fyyd",
            name        = "fyyd",
            description = "European podcast index — strong multilingual coverage",
            category    = "podcast",
            icon        = "🌍",
            placeholder = "e.g. netzpolitik, technology",
        ),
    ]

    async def search(self, query: str, source_id: str, limit: int = 20, **kwargs) -> list[SearchResult]:
        if source_id == "itunes":
            return await self._itunes(query, limit)
        if source_id == "podcast_index":
            return await self._podcast_index(query, limit)
        if source_id == "gpodder":
            return await self._gpodder(query, limit)
        if source_id == "fyyd":
            return await self._fyyd(query, limit)
        return []

    # ── Per-source implementations ────────────────────────────────────────────

    async def _itunes(self, q: str, limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={"media": "podcast", "entity": "podcast", "term": q, "limit": limit},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("iTunes search failed: %s", exc)
            return []
        return [
            SearchResult(
                feed_url    = item["feedUrl"],
                title       = item.get("collectionName"),
                description = item.get("artistName"),
                website_url = item.get("collectionViewUrl"),
                subscribers = item.get("trackCount"),
                language    = item.get("primaryGenreName"),
                cover_url   = item.get("artworkUrl600") or item.get("artworkUrl100"),
            )
            for item in resp.json().get("results", [])
            if item.get("feedUrl")
        ]

    async def _podcast_index(self, q: str, limit: int) -> list[SearchResult]:
        key    = os.getenv("PODCAST_INDEX_KEY", "")
        secret = os.getenv("PODCAST_INDEX_SECRET", "")
        if not key or not secret:
            logger.info("Podcast Index skipped — PODCAST_INDEX_KEY / SECRET not set")
            return []
        ts   = str(int(time.time()))
        auth = hashlib.sha256(f"{key}{secret}{ts}".encode()).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.podcastindex.org/api/1.0/search/byterm",
                    params={"q": q, "max": limit},
                    headers={**_HEADERS, "X-Auth-Key": key, "X-Auth-Date": ts, "Authorization": auth},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Podcast Index search failed: %s", exc)
            return []
        return [
            SearchResult(
                feed_url    = item["url"],
                title       = item.get("title"),
                description = item.get("description"),
                website_url = item.get("link"),
                subscribers = item.get("episodeCount"),
                language    = item.get("language"),
                cover_url   = item.get("image") or item.get("artwork"),
            )
            for item in resp.json().get("feeds", [])
            if item.get("url")
        ]

    async def _gpodder(self, q: str, limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get("https://gpodder.net/search.json", params={"q": q})
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("gpodder search failed: %s", exc)
            return []
        items = resp.json() if isinstance(resp.json(), list) else []
        return [
            SearchResult(
                feed_url    = item["url"],
                title       = item.get("title"),
                description = item.get("description"),
                website_url = item.get("website"),
                subscribers = item.get("subscribers"),
                cover_url   = item.get("logo_url") or item.get("scaled_logo_url"),
            )
            for item in items[:limit]
            if item.get("url")
        ]

    async def _fyyd(self, q: str, limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    "https://api.fyyd.de/0.2/search/podcast",
                    params={"term": q, "count": limit},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("fyyd search failed: %s", exc)
            return []
        items = resp.json().get("data", [])
        if not isinstance(items, list):
            items = []
        return [
            SearchResult(
                feed_url    = item["xmlURL"],
                title       = item.get("title"),
                description = item.get("description"),
                website_url = item.get("htmlURL"),
                subscribers = item.get("episode_count"),
                language    = item.get("language"),
                cover_url   = item.get("layoutImageURL") or item.get("smallImageURL"),
            )
            for item in items
            if item.get("xmlURL")
        ]
