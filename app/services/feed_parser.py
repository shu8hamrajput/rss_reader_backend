import asyncio
import feedparser
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from ..models import Article, Feed
from .article_fetcher import fetch_full_content
from .url_safety import assert_public_url

_FETCH_SEMAPHORE = asyncio.Semaphore(5)


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

    feed_info = parsed.feed
    if not feed.title:
        feed.title = feed_info.get("title") or feed.url
    feed.description = feed_info.get("subtitle") or feed_info.get("description")
    feed.site_url = feed_info.get("link")
    icon = feed_info.get("icon") or feed_info.get("image", {}).get("href")
    if icon:
        feed.icon_url = icon

    existing_guids: set[str] = {
        row[0]
        for row in db.query(Article.guid).filter(Article.feed_id == feed.id).all()
    }

    new_articles: list[Article] = []
    transcript_urls: list[str | None] = []
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

        # YouTube channel Atom feeds use <media:group> instead of <enclosure> —
        # mark entries as playable "podcast" episodes via the embedded player.
        if media_type is None and "youtube.com/feeds/videos.xml" in feed.url:
            media_type = "video/youtube"
            media_url = entry.get("link")

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

        # iTunes podcast metadata
        episode_number = str(entry.get('itunes_episode', '') or '').strip() or None
        itunes_author = (entry.get('itunes_author') or entry.get('author') or '').strip()[:256] or None
        # Prefer itunes:image for cover art over generic thumbnail
        itunes_image = entry.get('itunes_image', {})
        if isinstance(itunes_image, dict):
            itunes_image_url = itunes_image.get('href') or itunes_image.get('url')
        else:
            itunes_image_url = None

        thumbnail = _get_thumbnail(entry) or itunes_image_url

        # Podcast transcript URL (Podcasting 2.0 namespace + some RSS extensions)
        transcript_url: str | None = None
        for t in entry.get("podcast_transcript", []) or []:
            if isinstance(t, dict):
                url_val = t.get("url") or t.get("href")
                if url_val:
                    transcript_url = url_val
                    break
        if not transcript_url:
            # Some feeds use a simple string or the transcript tag directly
            raw_t = entry.get("podcast_transcript")
            if isinstance(raw_t, str) and raw_t.startswith("http"):
                transcript_url = raw_t

        article = Article(
            feed_id=feed.id,
            guid=guid,
            title=entry.get("title"),
            url=entry.get("link"),
            author=entry.get("author"),
            summary=entry.get("summary"),
            content=_get_content(entry),
            thumbnail_url=thumbnail,
            published_at=_parse_date(entry),
            media_type=media_type,
            media_url=media_url,
            duration_seconds=duration_seconds,
            episode_number=episode_number,
            itunes_author=itunes_author,
        )
        db.add(article)
        new_articles.append(article)
        transcript_urls.append(transcript_url)
        existing_guids.add(guid)

    # Flush to assign IDs before fetching full content + transcripts
    if new_articles:
        db.flush()
        await _fetch_full_content_for_articles(new_articles)
        await _fetch_transcripts_for_articles(new_articles, transcript_urls)

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
    """Concurrently fetch full BeautifulSoup content for a list of articles (max 5 at a time)."""
    async def _fetch_one(article: Article) -> None:
        if not article.url:
            return
        async with _FETCH_SEMAPHORE:
            article.full_content = await fetch_full_content(article.url)

    await asyncio.gather(*(_fetch_one(a) for a in articles))


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
