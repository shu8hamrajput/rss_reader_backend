"""
YouTube feed plugin.

Handles feeds from youtube.com/feeds/videos.xml (channel/playlist RSS).
Adds: video thumbnails, duration, chapter extraction, Shorts detection,
transcript fetching via the timedtext API (no API key required).

URL normalisation: converts channel pages, @handle URLs, and video URLs
to the canonical RSS feed URL.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .base import DiscoveredFeed, FeedPlugin, ParsedArticle, ParsedFeed, SearchResult, SearchSourceMeta
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



_YT_CHANNEL_RE = re.compile(
    r"youtube\.com/(?:channel/|(c/|user/|@))([\w@.-]+)", re.IGNORECASE
)
_HEADERS = {"User-Agent": "RSSReader/1.0"}


def _yt_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _extract_channel_id_from_html(html: str) -> str | None:
    for pattern in [
        r'"channelId"\s*:\s*"(UC[\w-]{22})"',
        r'"externalChannelId"\s*:\s*"(UC[\w-]{22})"',
        r'channel_id=(UC[\w-]{22})',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


class YouTubePlugin(FeedPlugin):
    name         = "youtube"
    display_name = "YouTube"
    description  = "YouTube channels and playlists via RSS"
    icon_emoji   = "▶️"

    search_sources = [
        SearchSourceMeta(
            id          = "youtube",
            name        = "YouTube",
            description = "Find channels by name or paste @handle / channel URL",
            category    = "video",
            icon        = "▶️",
            placeholder = "e.g. Lex Fridman, @fireship",
        ),
    ]

    def can_handle(self, url: str) -> bool:
        return "youtube.com/feeds/videos.xml" in url

    def normalize_url(self, url: str) -> str:
        """Convert channel/playlist/video/handle URLs to the Atom feed URL.

        For handles and video URLs that require a page fetch, returns the input
        unchanged — callers must use resolve_url() for async resolution.
        """
        if "youtube.com/feeds/videos.xml" in url:
            return url
        m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", url)
        if m:
            return _yt_rss_url(m.group(1))
        m = re.search(r"[?&]list=(PL[\w-]+)", url)
        if m:
            return f"https://www.youtube.com/feeds/videos.xml?playlist_id={m.group(1)}"
        return url

    async def resolve_url(self, url: str) -> str | None:
        """Async resolution for handles and video URLs that need a page fetch.

        Returns the RSS feed URL, or None if the channel ID couldn't be found.
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
            return None

        path = parsed.path.rstrip("/")

        # Playlist — no fetch needed
        pl_m = re.search(r"[?&]list=(PL[\w-]+)", parsed.query)
        if pl_m:
            return f"https://www.youtube.com/feeds/videos.xml?playlist_id={pl_m.group(1)}"

        # Direct channel ID
        m = re.match(r"^/channel/(UC[\w-]{22})$", path, re.IGNORECASE)
        if m:
            return _yt_rss_url(m.group(1))

        # Handle / user / custom name / video → fetch page
        if re.match(r"^/(@[\w.-]+|user/[\w.-]+|c/[\w.-]+|watch|shorts/[\w-]+)$", path, re.IGNORECASE) \
                or "youtu.be" in parsed.netloc or "/watch" in path:
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, headers=_HEADERS) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                channel_id = _extract_channel_id_from_html(resp.text)
                if channel_id:
                    return _yt_rss_url(channel_id)
            except Exception as exc:
                logger.debug("YouTube channel ID fetch failed for %s: %s", url, exc)
        return None

    async def search(self, query: str, source_id: str, limit: int = 20, **kwargs) -> list[SearchResult]:
        import os
        q = query.strip()

        # Keyless: @handle or youtube.com URL
        maybe_url = (
            q if q.startswith("http")
            else f"https://www.youtube.com/{q}" if q.startswith("@")
            else None
        )
        if maybe_url:
            rss_url = await self.resolve_url(maybe_url)
            if rss_url:
                channel_id_m = re.search(r"channel_id=(UC[\w-]{22})", rss_url)
                channel_id = channel_id_m.group(1) if channel_id_m else None
                return [SearchResult(
                    feed_url    = rss_url,
                    title       = q.lstrip("@"),
                    description = f"YouTube channel · {q}",
                    website_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else maybe_url,
                )]
            if q.startswith("http"):
                return []

        api_key = os.getenv("YOUTUBE_API_KEY", "")
        if not api_key:
            return [SearchResult(
                feed_url    = "",
                title       = "YouTube API key not configured",
                description = "Paste youtube.com/@handle to subscribe directly, or set YOUTUBE_API_KEY for full search.",
            )]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "type": "channel", "q": q,
                            "maxResults": min(limit, 50), "key": api_key},
                    headers=_HEADERS,
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("YouTube API search failed: %s", exc)
            return []

        results = []
        for item in resp.json().get("items", []):
            channel_id = item.get("id", {}).get("channelId")
            if not channel_id:
                continue
            snippet = item.get("snippet", {})
            thumbnails = snippet.get("thumbnails", {})
            results.append(SearchResult(
                feed_url    = _yt_rss_url(channel_id),
                title       = snippet.get("title"),
                description = snippet.get("description"),
                website_url = f"https://www.youtube.com/channel/{channel_id}",
                cover_url   = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url"),
            ))
        return results

    async def discover(self, url: str) -> list[DiscoveredFeed]:
        rss_url = await self.resolve_url(url)
        if rss_url:
            return [DiscoveredFeed(feed_url=rss_url, title="YouTube channel", feed_type="atom")]
        return []

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

        # Pure parsing — no enrichment here. TranscriptEnricher handles transcripts.
        # media_url stored in tags so TranscriptEnricher can extract the video_id.
        articles: list[ParsedArticle] = []

        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not guid:
                continue

            video_id  = _extract_video_id(entry)
            duration  = _yt_duration(entry)
            summary   = entry.get("summary") or ""
            title_str = entry.get("title") or ""
            is_short  = "#shorts" in title_str.lower() or (duration is not None and duration <= 60)
            chapters  = _chapters_html(summary, video_id) if video_id and summary else None
            content   = (chapters or "") + f"<p>{summary}</p>" if summary else None

            articles.append(ParsedArticle(
                guid             = guid,
                title            = title_str or None,
                url              = entry.get("link"),
                author           = entry.get("author"),
                summary          = summary or None,
                content          = content,
                thumbnail_url    = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None,
                published_at     = _parse_date(entry),
                media_type       = "video/youtube",
                media_url        = entry.get("link"),
                duration_seconds = duration,
                episode_number   = "Short" if is_short else None,
            ))

        result.articles = articles
        return result, resp.status_code
