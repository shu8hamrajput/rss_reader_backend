import asyncio
import re
import feedparser
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from ..models import Article, Feed
from .article_fetcher import fetch_full_content
from .url_safety import assert_public_url

_FETCH_SEMAPHORE = asyncio.Semaphore(5)

_YT_VIDEO_ID_RE = re.compile(r"[?&]v=([\w-]{11})")
_YT_CHANNEL_ID_RE = re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"')
# Matches "0:00 Title", "1:23:45 Long section", "#00:00 Title" etc.
_YT_CHAPTER_RE = re.compile(r"^#?(\d+:\d{2}(?::\d{2})?)\s+(.+)", re.MULTILINE)


def _time_str_to_seconds(t: str) -> int:
    """Convert "MM:SS" or "HH:MM:SS" to total seconds."""
    parts = t.lstrip("#").split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 0


def _extract_chapters_html(description: str, video_id: str) -> str | None:
    """Parse YouTube timestamp chapters from a video description.

    Returns an HTML snippet with a chapter list where each item is a link
    that opens the video at that timestamp. Returns None if fewer than 2
    chapters are found (not a real chapter list).
    """
    matches = _YT_CHAPTER_RE.findall(description)
    if len(matches) < 2:
        return None
    items = []
    for time_str, title in matches:
        secs = _time_str_to_seconds(time_str)
        items.append(
            f'<li><a href="https://www.youtube.com/watch?v={video_id}&t={secs}" '
            f'data-yt-seek="{secs}" target="_blank" class="yt-chapter-link">'
            f'<span class="yt-chapter-time">{time_str}</span> {title.strip()}'
            f"</a></li>"
        )
    return (
        '<div class="yt-chapters">'
        "<p><strong>Chapters</strong></p>"
        "<ol>" + "".join(items) + "</ol>"
        "</div>"
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


def _get_content(entry: feedparser.FeedParserDict) -> str | None:
    if entry.get("content"):
        return entry.content[0].get("value")
    return entry.get("summary")


def _get_thumbnail(entry: feedparser.FeedParserDict, is_youtube: bool = False) -> str | None:
    # For YouTube, prefer the maxresdefault thumbnail which is always available
    # and much higher quality than what <media:thumbnail> reports.
    if is_youtube:
        link = entry.get("link") or ""
        m = _YT_VIDEO_ID_RE.search(link)
        if not m:
            # Also check the guid/id field
            m = _YT_VIDEO_ID_RE.search(entry.get("id") or "")
        if m:
            vid = m.group(1)
            return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

    media = entry.get("media_thumbnail") or entry.get("media_content")
    if media:
        return media[0].get("url")
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href") or enc.get("url")
    return None


async def _fetch_youtube_transcript(video_id: str) -> str | None:
    """Fetch auto-generated or manual transcript via YouTube's timedtext API (no key needed).

    Returns cleaned plain-text or None if unavailable / private / non-English.
    The transcript is stored as full_content so the reader can show spoken text.
    """
    url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang=en&fmt=json3"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=10.0,
            headers={"User-Agent": "RSSReader/1.0"},
        ) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            return None
        data = resp.json()
        lines: list[str] = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                text = seg.get("utf8", "").strip().replace("\n", " ")
                if text and text != "\n":
                    lines.append(text)
        text = " ".join(lines).strip()
        return text[:100_000] if text else None
    except Exception:
        return None


def _parse_yt_duration(entry: feedparser.FeedParserDict) -> int | None:
    """Extract video duration in seconds from YouTube Atom <yt:duration seconds="N"/> tag."""
    # feedparser maps yt:duration to entry.yt_duration or entry['yt_duration']
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
    # feedparser sometimes exposes it as a top-level attribute
    raw = entry.get("duration")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return None


async def _http_fetch(
    url: str,
    etag: str | None,
    last_modified: str | None,
) -> tuple[httpx.Response, bool]:
    assert_public_url(url)
    headers: dict[str, str] = {"User-Agent": "RSSReader/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
    return resp, resp.status_code == 304


async def _apply_parsed_to_feed(
    feed: Feed,
    parsed: feedparser.FeedParserDict,
    new_etag: str | None,
    new_last_modified: str | None,
    db: Session,
) -> int:
    """Apply already-fetched + parsed data to a single Feed row. Returns new article count.
    Fetches full article content via BeautifulSoup for each new article.
    Caller is responsible for db.commit()."""
    if new_etag:
        feed.etag = new_etag
    if new_last_modified:
        feed.last_modified = new_last_modified

    is_youtube = "youtube.com/feeds/videos.xml" in feed.url

    feed_info = parsed.feed
    if not feed.title:
        feed.title = feed_info.get("title") or feed.url
    feed.description = feed_info.get("subtitle") or feed_info.get("description")
    feed.site_url = feed_info.get("link")

    # Feed icon: generic feeds use <icon>/<image>; YouTube channels expose the
    # channel avatar as <logo> or <icon> in the Atom envelope.
    icon = (
        feed_info.get("icon")
        or feed_info.get("logo")
        or (feed_info.get("image") or {}).get("href")
        or (feed_info.get("image") or {}).get("url")
    )
    if icon:
        feed.icon_url = icon

    existing_guids: set[str] = {
        row[0]
        for row in db.query(Article.guid).filter(Article.feed_id == feed.id).all()
    }

    new_articles: list[Article] = []
    transcript_urls: list[str | None] = []   # podcast transcript URLs (audio feeds)
    yt_video_ids: list[str | None] = []      # YouTube video IDs for transcript fetch

    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not guid or guid in existing_guids:
            continue

        enclosures = entry.get('enclosures', [])
        media_type = media_url = None
        duration_seconds = None

        if enclosures:
            enc = enclosures[0]
            media_type = enc.get('type')
            media_url = enc.get('href') or enc.get('url')

        yt_video_id: str | None = None
        if is_youtube:
            # YouTube Atom: <media:group> carries the video link & thumbnail.
            # Mark as video/youtube so the reader uses the embedded player.
            media_type = "video/youtube"
            media_url = entry.get("link")
            # Prefer the yt:videoId tag (feedparser maps it to yt_videoid)
            yt_video_id = (
                entry.get("yt_videoid")
                or (lambda m: m.group(1) if m else None)(
                    _YT_VIDEO_ID_RE.search(media_url or guid or "")
                )
            )
            # Duration: YouTube Atom exposes <yt:duration seconds="N">
            duration_seconds = _parse_yt_duration(entry)

        else:
            # Podcast duration from itunes:duration
            raw_dur = entry.get('itunes_duration')
            if raw_dur and isinstance(raw_dur, str):
                parts = raw_dur.split(':')
                try:
                    if len(parts) == 3:
                        duration_seconds = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
                    elif len(parts) == 2:
                        duration_seconds = int(parts[0])*60 + int(parts[1])
                    else:
                        duration_seconds = int(raw_dur)
                except ValueError:
                    pass

        # iTunes / podcast metadata
        episode_number = str(entry.get('itunes_episode', '') or '').strip() or None
        itunes_author = (entry.get('itunes_author') or entry.get('author') or '').strip()[:256] or None
        itunes_image = entry.get('itunes_image', {})
        itunes_image_url = (
            (itunes_image.get('href') or itunes_image.get('url'))
            if isinstance(itunes_image, dict) else None
        )
        thumbnail = _get_thumbnail(entry, is_youtube=is_youtube) or itunes_image_url

        # For YouTube the <media:description> is surfaced as entry.summary.
        summary = entry.get("summary") or ""

        # Detect YouTube Shorts (title has #Shorts tag OR duration ≤ 60 s).
        # Stored in episode_number so ArticleList can badge them and routing
        # rules can auto-skip them.
        title_str = entry.get("title") or ""
        is_short = is_youtube and (
            "#shorts" in title_str.lower()
            or (duration_seconds is not None and duration_seconds <= 60)
        )

        if is_youtube and yt_video_id and summary:
            # Prepend chapter list (if found) then show description
            chapters_html = _extract_chapters_html(summary, yt_video_id)
            content = (chapters_html or "") + f"<p>{summary}</p>"
        else:
            content = _get_content(entry) if not is_youtube else (summary or None)

        # Podcast transcript URL (Podcasting 2.0 namespace)
        transcript_url: str | None = None
        for t in entry.get("podcast_transcript", []) or []:
            if isinstance(t, dict):
                url_val = t.get("url") or t.get("href")
                if url_val:
                    transcript_url = url_val
                    break
        if not transcript_url:
            raw_t = entry.get("podcast_transcript")
            if isinstance(raw_t, str) and raw_t.startswith("http"):
                transcript_url = raw_t

        article = Article(
            feed_id=feed.id,
            guid=guid,
            title=title_str or entry.get("title"),
            url=entry.get("link"),
            author=entry.get("author"),
            summary=summary or None,
            content=content,
            thumbnail_url=thumbnail,
            published_at=_parse_date(entry),
            media_type=media_type,
            media_url=media_url,
            duration_seconds=duration_seconds,
            episode_number="Short" if is_short else episode_number,
            itunes_author=itunes_author,
        )
        db.add(article)
        new_articles.append(article)
        transcript_urls.append(transcript_url)
        yt_video_ids.append(yt_video_id)
        existing_guids.add(guid)

    # Flush to assign IDs before async fetches
    if new_articles:
        db.flush()
        await _fetch_full_content_for_articles(new_articles)
        await _fetch_transcripts_for_articles(new_articles, transcript_urls)
        await _fetch_youtube_transcripts_for_articles(new_articles, yt_video_ids)

    feed.last_fetched_at = datetime.now(timezone.utc)
    return len(new_articles)


async def _fetch_transcript(url: str) -> str | None:
    """Fetch a podcast transcript (VTT/SRT/plain) and return its text content."""
    try:
        assert_public_url(url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "RSSReader/1.0"})
        resp.raise_for_status()
        text = resp.text
        # Strip VTT/SRT timing lines, leaving only spoken text
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip WEBVTT header, cue identifiers, timestamps (00:00:00.000 --> ...)
            if line in ("WEBVTT",) or "-->" in line or line.isdigit():
                continue
            lines.append(line)
        return " ".join(lines)[:100_000] if lines else None
    except Exception:
        return None


async def _fetch_transcripts_for_articles(articles: list[Article], urls: list[str | None]) -> None:
    """For podcast articles with a transcript URL, fetch and store transcript as full_content."""
    async def _fetch_one(article: Article, url: str) -> None:
        if article.full_content:
            return  # don't overwrite BeautifulSoup-fetched content
        async with _FETCH_SEMAPHORE:
            text = await _fetch_transcript(url)
            if text:
                article.full_content = text

    tasks = [
        _fetch_one(a, u)
        for a, u in zip(articles, urls)
        if u and a.media_type and a.media_type.startswith("audio/")
    ]
    if tasks:
        await asyncio.gather(*tasks)


async def _fetch_full_content_for_articles(articles: list[Article]) -> None:
    """Concurrently fetch full BeautifulSoup content for non-YouTube articles."""
    async def _fetch_one(article: Article) -> None:
        if not article.url:
            return
        if article.media_type == "video/youtube":
            # YouTube pages return ~400 KB of JavaScript; scraping them is useless.
            # Transcripts are fetched separately via _fetch_youtube_transcripts_for_articles.
            return
        async with _FETCH_SEMAPHORE:
            article.full_content = await fetch_full_content(article.url)

    await asyncio.gather(*(_fetch_one(a) for a in articles))


async def _fetch_youtube_transcripts_for_articles(
    articles: list[Article], video_ids: list[str | None]
) -> None:
    """Fetch auto-generated transcripts for new YouTube video articles.

    Uses YouTube's free timedtext API (no key required).  The transcript text
    is stored as full_content so the reader can display spoken text and highlights
    work on it just like article body text.
    """
    async def _fetch_one(article: Article, video_id: str) -> None:
        async with _FETCH_SEMAPHORE:
            text = await _fetch_youtube_transcript(video_id)
            if text:
                article.full_content = text

    tasks = [
        _fetch_one(a, vid)
        for a, vid in zip(articles, video_ids)
        if vid and a.media_type == "video/youtube" and not a.full_content
    ]
    if tasks:
        await asyncio.gather(*tasks)


async def refresh_feed(feed: Feed, db: Session) -> int:
    """Fetch the feed, store new articles, update HTTP cache headers. Returns new article count."""
    resp, not_modified = await _http_fetch(feed.url, feed.etag, feed.last_modified)

    if not_modified:
        feed.last_fetched_at = datetime.now(timezone.utc)
        db.commit()
        return 0

    resp.raise_for_status()

    parsed = feedparser.parse(resp.text)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Feed parse error: {parsed.get('bozo_exception')}")

    new_count = await _apply_parsed_to_feed(
        feed, parsed,
        resp.headers.get("ETag"),
        resp.headers.get("Last-Modified"),
        db,
    )
    db.commit()
    return new_count


async def refresh_url_for_all_subscribers(feeds: list[Feed], db: Session) -> dict[int, int]:
    """Fetch a feed URL once and apply new articles to every subscriber Feed row.

    Returns a mapping of {feed_id: new_article_count} for each feed in the list.
    Uses the most-recently-fetched subscriber's cache headers for the conditional GET,
    so the remote server is hit at most once per URL regardless of subscriber count.
    """
    if not feeds:
        return {}

    _EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
    reference = max(feeds, key=lambda f: f.last_fetched_at or _EPOCH)
    resp, not_modified = await _http_fetch(feeds[0].url, reference.etag, reference.last_modified)

    now = datetime.now(timezone.utc)
    if not_modified:
        for feed in feeds:
            feed.last_fetched_at = now
        db.commit()
        return {f.id: 0 for f in feeds}

    resp.raise_for_status()

    parsed = feedparser.parse(resp.text)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Feed parse error: {parsed.get('bozo_exception')}")

    new_etag = resp.headers.get("ETag")
    new_last_modified = resp.headers.get("Last-Modified")

    results: dict[int, int] = {}
    for feed in feeds:
        results[feed.id] = await _apply_parsed_to_feed(feed, parsed, new_etag, new_last_modified, db)

    db.commit()
    return results
