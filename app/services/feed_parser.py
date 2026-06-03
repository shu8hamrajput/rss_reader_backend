import asyncio
import feedparser
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from ..models import Article, Feed
from .article_fetcher import fetch_full_content

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
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not guid or guid in existing_guids:
            continue
        article = Article(
            feed_id=feed.id,
            guid=guid,
            title=entry.get("title"),
            url=entry.get("link"),
            author=entry.get("author"),
            summary=entry.get("summary"),
            content=_get_content(entry),
            thumbnail_url=_get_thumbnail(entry),
            published_at=_parse_date(entry),
        )
        db.add(article)
        new_articles.append(article)
        existing_guids.add(guid)

    # Flush to assign IDs before fetching full content
    if new_articles:
        db.flush()
        await _fetch_full_content_for_articles(new_articles)

    feed.last_fetched_at = datetime.now(timezone.utc)
    return len(new_articles)


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
