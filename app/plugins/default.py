"""
Default feed plugin — handles generic RSS/Atom feeds.

Also handles podcast feeds (feeds with audio enclosures and iTunes namespace)
since podcast detection is content-based (needs to inspect entries), not URL-based.

Enrichments applied:
  - BeautifulSoup full-content fetch for article feeds
  - iTunes namespace extraction (duration, episode number, author, image)
  - Podcasting 2.0 transcript fetch (podcast:transcript tag)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .base import FeedPlugin, ParsedArticle, ParsedFeed
from ..services.url_safety import assert_public_url
from ..services.article_fetcher import fetch_full_content

logger = logging.getLogger(__name__)

_FETCH_SEM = asyncio.Semaphore(5)


def _parse_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        val = entry.get(attr)
        if val:
            try:
                return parsedate_to_datetime(val)
            except Exception:
                pass
    return None


def _get_content(entry: feedparser.FeedParserDict) -> str | None:
    if entry.get("content"):
        return entry.content[0].get("value")
    return entry.get("summary")


def _get_thumbnail(entry: feedparser.FeedParserDict) -> str | None:
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if media:
        return media[0].get("url")
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href") or enc.get("url")
    return None


def _parse_itunes_duration(raw: str) -> int | None:
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(raw)
    except (ValueError, IndexError):
        return None


def _podcast_transcript_url(entry: feedparser.FeedParserDict) -> str | None:
    for t in entry.get("podcast_transcript", []) or []:
        if isinstance(t, dict):
            url = t.get("url") or t.get("href")
            if url:
                return url
    raw = entry.get("podcast_transcript")
    if isinstance(raw, str) and raw.startswith("http"):
        return raw
    return None


async def _fetch_transcript(url: str) -> str | None:
    try:
        assert_public_url(url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "RSSReader/1.0"})
        resp.raise_for_status()
        text = resp.text
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line in ("WEBVTT",) or "-->" in line or line.isdigit():
                continue
            lines.append(line)
        return " ".join(lines)[:100_000] if lines else None
    except Exception as exc:
        logger.debug("Podcast transcript fetch failed for %s: %s", url, exc)
        return None


class DefaultPlugin(FeedPlugin):
    name         = "default"
    display_name = "RSS / Atom"
    description  = "Generic RSS and Atom feeds (articles, newsletters, podcasts)"
    icon_emoji   = "📰"

    def can_handle(self, url: str) -> bool:
        return True  # fallback — matches everything

    async def fetch(
        self,
        url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[ParsedFeed | None, int]:
        assert_public_url(url)
        headers: dict[str, str] = {"User-Agent": "RSSReader/1.0"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code == 304:
            return None, 304

        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"Feed parse error: {parsed.get('bozo_exception')}")

        feed_info = parsed.feed
        result = ParsedFeed(
            title       = feed_info.get("title"),
            description = feed_info.get("subtitle") or feed_info.get("description"),
            site_url    = feed_info.get("link"),
            icon_url    = (
                feed_info.get("icon")
                or feed_info.get("logo")
                or (feed_info.get("image") or {}).get("href")
                or (feed_info.get("image") or {}).get("url")
            ),
            etag          = resp.headers.get("ETag"),
            last_modified = resp.headers.get("Last-Modified"),
        )

        articles: list[ParsedArticle] = []
        transcript_tasks: list[tuple[int, str]] = []   # (article_idx, transcript_url)
        content_fetch_idxs: list[int] = []              # article indices needing full-content

        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not guid:
                continue

            enclosures = entry.get("enclosures", [])
            media_type = media_url = None
            if enclosures:
                enc = enclosures[0]
                media_type = enc.get("type")
                media_url  = enc.get("href") or enc.get("url")

            duration_seconds: int | None = None
            raw_dur = entry.get("itunes_duration")
            if raw_dur and isinstance(raw_dur, str):
                duration_seconds = _parse_itunes_duration(raw_dur)

            itunes_image = entry.get("itunes_image", {})
            itunes_image_url = (
                (itunes_image.get("href") or itunes_image.get("url"))
                if isinstance(itunes_image, dict) else None
            )

            art = ParsedArticle(
                guid             = guid,
                title            = entry.get("title"),
                url              = entry.get("link"),
                author           = entry.get("author"),
                summary          = entry.get("summary") or None,
                content          = _get_content(entry),
                thumbnail_url    = _get_thumbnail(entry) or itunes_image_url,
                published_at     = _parse_date(entry),
                media_type       = media_type,
                media_url        = media_url,
                duration_seconds = duration_seconds,
                episode_number   = str(entry.get("itunes_episode") or "").strip() or None,
                itunes_author    = (entry.get("itunes_author") or entry.get("author") or "").strip()[:256] or None,
            )
            idx = len(articles)
            articles.append(art)

            # Queue transcript fetch for podcast episodes
            transcript_url = _podcast_transcript_url(entry)
            if transcript_url and media_type and media_type.startswith("audio/"):
                transcript_tasks.append((idx, transcript_url))

            # Queue full-content fetch for article URLs (non-podcast)
            if art.url and not media_type:
                content_fetch_idxs.append(idx)

        # Run async enrichments concurrently
        async def _do_transcript(idx: int, url: str) -> None:
            async with _FETCH_SEM:
                text = await _fetch_transcript(url)
                if text:
                    articles[idx].full_content = text

        async def _do_content(idx: int) -> None:
            art = articles[idx]
            if not art.url:
                return
            async with _FETCH_SEM:
                art.full_content = await fetch_full_content(art.url)

        tasks: list = [_do_transcript(i, u) for i, u in transcript_tasks]
        tasks += [_do_content(i) for i in content_fetch_idxs]
        if tasks:
            await asyncio.gather(*tasks)

        result.articles = articles
        return result, resp.status_code
