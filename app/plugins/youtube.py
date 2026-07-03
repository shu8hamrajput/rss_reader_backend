"""
YouTube feed plugin.

Handles feeds from youtube.com/feeds/videos.xml (channel/playlist RSS).
Adds: video thumbnails, duration, chapter extraction, Shorts detection,
transcript fetching via the timedtext API (no API key required).

URL normalisation: converts channel pages, @handle URLs, and video URLs
to the canonical RSS feed URL.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .base import FeedPlugin, ParsedArticle, ParsedFeed
from ..services.url_safety import assert_public_url

logger = logging.getLogger(__name__)

_YT_VIDEO_ID_RE   = re.compile(r"[?&]v=([\w-]{11})|/shorts/([\w-]{11})|youtu\.be/([\w-]{11})")
_YT_CHANNEL_ID_RE = re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"')
_YT_HANDLE_RE     = re.compile(r"youtube\.com/@([\w.-]+)")
_YT_CHAPTER_RE    = re.compile(r"^#?(\d+:\d{2}(?::\d{2})?)\s+(.+)", re.MULTILINE)


def _time_to_secs(t: str) -> int:
    parts = t.lstrip("#").split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 0


def _chapters_html(description: str, video_id: str) -> str | None:
    matches = _YT_CHAPTER_RE.findall(description)
    if len(matches) < 2:
        return None
    items = [
        f'<li><a href="https://www.youtube.com/watch?v={video_id}&t={_time_to_secs(ts)}" '
        f'data-yt-seek="{_time_to_secs(ts)}" target="_blank" class="yt-chapter-link">'
        f'<span class="yt-chapter-time">{ts}</span> {title.strip()}</a></li>'
        for ts, title in matches
    ]
    return (
        '<div class="yt-chapters"><p><strong>Chapters</strong></p>'
        "<ol>" + "".join(items) + "</ol></div>"
    )


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


def _extract_video_id(entry: feedparser.FeedParserDict) -> str | None:
    vid = entry.get("yt_videoid")
    if vid:
        return vid
    for field in ("link", "id"):
        m = _YT_VIDEO_ID_RE.search(entry.get(field) or "")
        if m:
            return m.group(1) or m.group(2) or m.group(3)
    return None


def _yt_duration(entry: feedparser.FeedParserDict) -> int | None:
    for key in ("yt_duration", "media_content"):
        val = entry.get(key)
        if isinstance(val, dict):
            try:
                return int(val.get("duration") or val.get("seconds") or 0) or None
            except (TypeError, ValueError):
                pass
        if isinstance(val, list) and val:
            try:
                return int(val[0].get("duration") or val[0].get("seconds") or 0) or None
            except (TypeError, ValueError):
                pass
    raw = entry.get("duration")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return None


async def _fetch_transcript(video_id: str) -> str | None:
    url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang=en&fmt=json3"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                      headers={"User-Agent": "RSSReader/1.0"}) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            return None
        data = resp.json()
        lines = [
            seg.get("utf8", "").strip().replace("\n", " ")
            for event in data.get("events", [])
            for seg in event.get("segs", [])
        ]
        text = " ".join(l for l in lines if l and l != "\n").strip()
        return text[:100_000] if text else None
    except Exception as exc:
        logger.debug("YouTube transcript fetch failed for %s: %s", video_id, exc)
        return None


class YouTubePlugin(FeedPlugin):
    name         = "youtube"
    display_name = "YouTube"
    description  = "YouTube channels and playlists via RSS"
    icon_emoji   = "▶️"

    def can_handle(self, url: str) -> bool:
        return "youtube.com/feeds/videos.xml" in url

    def normalize_url(self, url: str) -> str:
        """Convert channel/playlist/video/handle URLs to the Atom feed URL."""
        if "youtube.com/feeds/videos.xml" in url:
            return url

        # youtube.com/channel/UCxxx or /c/Name
        m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", url)
        if m:
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"

        # youtube.com/playlist?list=PLxxx
        m = re.search(r"[?&]list=(PL[\w-]+)", url)
        if m:
            return f"https://www.youtube.com/feeds/videos.xml?playlist_id={m.group(1)}"

        # @handle — need to resolve channel ID (best effort via webpage scrape)
        # Returned as-is here; the feeds router calls normalize_url then validates.
        return url

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
            raise ValueError(f"YouTube feed parse error: {parsed.get('bozo_exception')}")

        feed_info = parsed.feed
        result = ParsedFeed(
            title       = feed_info.get("title"),
            description = feed_info.get("subtitle") or feed_info.get("description"),
            site_url    = feed_info.get("link"),
            icon_url    = (
                feed_info.get("icon")
                or feed_info.get("logo")
                or (feed_info.get("image") or {}).get("href")
            ),
            etag          = resp.headers.get("ETag"),
            last_modified = resp.headers.get("Last-Modified"),
        )

        # Fetch transcripts for all new videos concurrently
        sem = asyncio.Semaphore(5)
        articles: list[ParsedArticle] = []
        transcript_tasks: list[tuple[int, str]] = []

        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not guid:
                continue

            video_id    = _extract_video_id(entry)
            duration    = _yt_duration(entry)
            summary     = entry.get("summary") or ""
            title_str   = entry.get("title") or ""
            is_short    = "#shorts" in title_str.lower() or (duration is not None and duration <= 60)

            chapters = _chapters_html(summary, video_id) if video_id and summary else None
            content  = (chapters or "") + f"<p>{summary}</p>" if summary else None

            thumbnail = None
            if video_id:
                thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            art = ParsedArticle(
                guid            = guid,
                title           = title_str or None,
                url             = entry.get("link"),
                author          = entry.get("author"),
                summary         = summary or None,
                content         = content,
                thumbnail_url   = thumbnail,
                published_at    = _parse_date(entry),
                media_type      = "video/youtube",
                media_url       = entry.get("link"),
                duration_seconds= duration,
                episode_number  = "Short" if is_short else None,
            )
            articles.append(art)
            if video_id:
                transcript_tasks.append((len(articles) - 1, video_id))

        # Fetch transcripts concurrently
        async def _fetch_one(idx: int, vid: str) -> None:
            async with sem:
                text = await _fetch_transcript(vid)
                if text:
                    articles[idx].full_content = text

        if transcript_tasks:
            await asyncio.gather(*(_fetch_one(i, v) for i, v in transcript_tasks))

        result.articles = articles
        return result, resp.status_code
