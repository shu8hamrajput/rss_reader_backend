import feedparser
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from ..models import Article, Feed


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


async def fetch_and_parse(url: str) -> feedparser.FeedParserDict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url, headers={"User-Agent": "RSSReader/1.0"})
        resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Feed parse error: {parsed.get('bozo_exception')}")
    return parsed


async def refresh_feed(feed: Feed, db: Session) -> int:
    """Fetch the feed, store new articles, update HTTP cache headers. Returns new article count."""
    headers: dict[str, str] = {"User-Agent": "RSSReader/1.0"}

    # Conditional GET — skip re-parsing if nothing changed server-side
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(feed.url, headers=headers)

    if resp.status_code == 304:
        # Not modified — update timestamp and return
        feed.last_fetched_at = datetime.now(timezone.utc)
        db.commit()
        return 0

    resp.raise_for_status()

    # Persist new cache headers for next request
    if resp.headers.get("ETag"):
        feed.etag = resp.headers["ETag"]
    if resp.headers.get("Last-Modified"):
        feed.last_modified = resp.headers["Last-Modified"]

    parsed = feedparser.parse(resp.text)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Feed parse error: {parsed.get('bozo_exception')}")

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

    new_count = 0
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not guid or guid in existing_guids:
            continue
        db.add(Article(
            feed_id=feed.id,
            guid=guid,
            title=entry.get("title"),
            url=entry.get("link"),
            author=entry.get("author"),
            summary=entry.get("summary"),
            content=_get_content(entry),
            thumbnail_url=_get_thumbnail(entry),
            published_at=_parse_date(entry),
        ))
        existing_guids.add(guid)
        new_count += 1

    feed.last_fetched_at = datetime.now(timezone.utc)
    db.commit()
    return new_count
