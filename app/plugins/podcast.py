"""
Podcast discovery plugin.

Podcast feeds are standard RSS/Atom with iTunes namespace extensions.
The DefaultPlugin already handles parsing them (enclosures, itunes:duration, etc.).

This plugin's role is search() only — it queries multiple podcast directories:
  - Apple Podcasts (iTunes) search API   — no key
  - gpodder.net API                      — no key
  - fyyd.de API                          — no key
  - Podcast Index API                    — free key from podcastindex.org

can_handle() returns False: podcast feeds are parsed by DefaultPlugin.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import httpx

from .base import FeedPlugin, ParsedFeed

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "RSSReader/1.0", "Accept": "application/json"}


class PodcastPlugin(FeedPlugin):
    name         = "podcast"
    display_name = "Podcasts"
    description  = "Search Apple Podcasts, Podcast Index, gpodder, and fyyd"
    icon_emoji   = "🎙️"

    def can_handle(self, url: str) -> bool:
        return False   # parsed by DefaultPlugin

    async def fetch(self, url, etag, last_modified) -> tuple[ParsedFeed | None, int]:
        raise NotImplementedError("PodcastPlugin delegates fetching to DefaultPlugin")

    async def search(self, query: str, source: str = "itunes", limit: int = 20, **kwargs) -> list[dict]:
        """Search podcast directories.

        source: "itunes" | "podcast_index" | "gpodder" | "fyyd"
        """
        if source == "itunes":
            return await self._search_itunes(query, limit)
        if source == "podcast_index":
            return await self._search_podcast_index(query, limit)
        if source == "gpodder":
            return await self._search_gpodder(query, limit)
        if source == "fyyd":
            return await self._search_fyyd(query, limit)
        return []

    # ── Per-source helpers ────────────────────────────────────────────────────

    async def _search_itunes(self, q: str, limit: int) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={"term": q, "media": "podcast", "limit": limit},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("iTunes search failed: %s", exc)
            return []

        results = []
        for item in resp.json().get("results", []):
            feed_url = item.get("feedUrl")
            if not feed_url:
                continue
            results.append({
                "feed_url":    feed_url,
                "title":       item.get("collectionName") or item.get("trackName"),
                "description": None,
                "website_url": item.get("collectionViewUrl"),
                "subscribers": item.get("trackCount"),
                "cover_url":   item.get("artworkUrl600") or item.get("artworkUrl100"),
                "language":    item.get("primaryGenreName"),
                "source":      "itunes",
            })
        return results

    async def _search_podcast_index(self, q: str, limit: int) -> list[dict]:
        api_key    = os.getenv("PODCAST_INDEX_KEY", "")
        api_secret = os.getenv("PODCAST_INDEX_SECRET", "")
        if not api_key or not api_secret:
            logger.info("Podcast Index search skipped — no API credentials")
            return []

        ts        = str(int(time.time()))
        auth_hash = hashlib.sha256(f"{api_key}{api_secret}{ts}".encode()).hexdigest()
        headers   = {**_HEADERS, "X-Auth-Key": api_key, "X-Auth-Date": ts, "Authorization": auth_hash}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.podcastindex.org/api/1.0/search/byterm",
                    params={"q": q, "max": limit},
                    headers=headers,
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Podcast Index search failed: %s", exc)
            return []

        results = []
        for item in resp.json().get("feeds", []):
            feed_url = item.get("url")
            if not feed_url:
                continue
            results.append({
                "feed_url":    feed_url,
                "title":       item.get("title"),
                "description": item.get("description"),
                "website_url": item.get("link"),
                "subscribers": item.get("episodeCount"),
                "cover_url":   item.get("image"),
                "language":    item.get("language"),
                "source":      "podcast_index",
            })
        return results

    async def _search_gpodder(self, q: str, limit: int) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
                resp = await client.get(
                    "https://gpodder.net/search.json",
                    params={"q": q, "pageSize": limit},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("gpodder search failed: %s", exc)
            return []

        results = []
        for item in resp.json():
            feed_url = item.get("url")
            if not feed_url:
                continue
            results.append({
                "feed_url":    feed_url,
                "title":       item.get("title"),
                "description": item.get("description"),
                "website_url": item.get("website"),
                "subscribers": item.get("subscribers"),
                "cover_url":   item.get("logo_url"),
                "source":      "gpodder",
            })
        return results

    async def _search_fyyd(self, q: str, limit: int) -> list[dict]:
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

        results = []
        for item in resp.json().get("data", []):
            feed_url = item.get("xmlURL") or item.get("feed_url")
            if not feed_url:
                continue
            results.append({
                "feed_url":    feed_url,
                "title":       item.get("title"),
                "description": item.get("description"),
                "website_url": item.get("htmlURL"),
                "subscribers": item.get("subscribers"),
                "cover_url":   item.get("imgURL"),
                "source":      "fyyd",
            })
        return results
