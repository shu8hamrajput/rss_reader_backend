"""
Default feed plugin — handles generic RSS/Atom feeds (articles, newsletters, podcasts).

This plugin is a pure parser: it extracts data from the feed XML and returns
ParsedArticle structs. Enrichment (full-text fetch, transcript download) is handled
by the enricher pipeline in app/enrichers/. See ADR-002.

Podcast transcript URLs are stored in article.tags as "transcript:<url>" so the
TranscriptEnricher can pick them up without re-parsing the feed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .base import FeedPlugin, ParsedArticle, ParsedFeed
from ..services.url_safety import assert_public_url

logger = logging.getLogger(__name__)


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


class DefaultPlugin(FeedPlugin):
    name         = "default"
    display_name = "RSS / Atom"
    description  = "Generic RSS and Atom feeds (articles, newsletters, podcasts)"
    icon_emoji   = "📰"

    def can_handle(self, url: str) -> bool:
        return True  # fallback

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
            title         = feed_info.get("title"),
            description   = feed_info.get("subtitle") or feed_info.get("description"),
            site_url      = feed_info.get("link"),
            icon_url      = (
                feed_info.get("icon") or feed_info.get("logo")
                or (feed_info.get("image") or {}).get("href")
                or (feed_info.get("image") or {}).get("url")
            ),
            etag          = resp.headers.get("ETag"),
            last_modified = resp.headers.get("Last-Modified"),
        )

        articles: list[ParsedArticle] = []
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

            transcript_url = _podcast_transcript_url(entry) if (
                media_type and media_type.startswith("audio/")
            ) else None

            articles.append(ParsedArticle(
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
                transcript_url   = transcript_url,
            ))

        result.articles = articles
        return result, resp.status_code
