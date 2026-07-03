"""
Feed parser — thin orchestration layer over the plugin system.

All feed-type-specific logic lives in app/plugins/. This module handles:
  - Dispatching to the right plugin via plugin_registry
  - Writing ParsedFeed / ParsedArticle structs to the database
  - Deduplication (existing GUIDs)
  - Updating Feed model fields (etag, last_modified, title, etc.)

External callers (tasks.py, routers/feeds.py) call refresh_feed() or
refresh_url_for_all_subscribers() — same signatures as before.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Article, Feed
from ..plugins import plugin_registry
from ..plugins.base import ParsedFeed

logger = logging.getLogger(__name__)


def _apply_parsed_feed_meta(feed: Feed, parsed: ParsedFeed) -> None:
    """Update feed metadata from a ParsedFeed (title, icon, cache headers, etc.)."""
    if parsed.etag:
        feed.etag = parsed.etag
    if parsed.last_modified:
        feed.last_modified = parsed.last_modified
    if not feed.title and parsed.title:
        feed.title = parsed.title
    if parsed.description is not None:
        feed.description = parsed.description
    if parsed.site_url:
        feed.site_url = parsed.site_url
    if parsed.icon_url:
        feed.icon_url = parsed.icon_url


def _write_articles(feed: Feed, parsed: ParsedFeed, db: Session) -> int:
    """Persist new articles from ParsedFeed into the database. Returns new count."""
    existing_guids: set[str] = {
        row[0]
        for row in db.query(Article.guid).filter(Article.feed_id == feed.id).all()
    }

    new_count = 0
    for art in parsed.articles:
        if not art.guid or art.guid in existing_guids:
            continue
        db.add(Article(
            feed_id          = feed.id,
            guid             = art.guid,
            title            = art.title,
            url              = art.url,
            author           = art.author,
            summary          = art.summary,
            content          = art.content,
            full_content     = art.full_content,
            thumbnail_url    = art.thumbnail_url,
            published_at     = art.published_at,
            media_type       = art.media_type,
            media_url        = art.media_url,
            duration_seconds = art.duration_seconds,
            episode_number   = art.episode_number,
            itunes_author    = art.itunes_author,
        ))
        existing_guids.add(art.guid)
        new_count += 1

    return new_count


async def refresh_feed(feed: Feed, db: Session) -> int:
    """Fetch the feed, store new articles, update HTTP cache headers.

    Returns the number of new articles added.
    Caller is NOT responsible for db.commit() — this function commits.
    """
    plugin = plugin_registry.get_plugin(feed.url)
    parsed, status = await plugin.fetch(feed.url, feed.etag, feed.last_modified)

    feed.last_fetched_at = datetime.now(timezone.utc)

    if parsed is None:  # 304 Not Modified
        db.commit()
        return 0

    _apply_parsed_feed_meta(feed, parsed)
    if not feed.plugin_name:
        feed.plugin_name = plugin.name
    new_count = _write_articles(feed, parsed, db)
    db.commit()
    return new_count


async def refresh_url_for_all_subscribers(feeds: list[Feed], db: Session) -> dict[int, int]:
    """Fetch a feed URL once and apply new articles to every subscriber Feed row.

    Returns {feed_id: new_article_count}.
    Fetches using the most-recently-updated subscriber's cache headers.
    """
    if not feeds:
        return {}

    _EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
    reference = max(feeds, key=lambda f: f.last_fetched_at or _EPOCH)

    plugin = plugin_registry.get_plugin(feeds[0].url)
    parsed, _ = await plugin.fetch(feeds[0].url, reference.etag, reference.last_modified)

    now = datetime.now(timezone.utc)
    if parsed is None:
        for feed in feeds:
            feed.last_fetched_at = now
        db.commit()
        return {f.id: 0 for f in feeds}

    results: dict[int, int] = {}
    for feed in feeds:
        feed.last_fetched_at = now
        _apply_parsed_feed_meta(feed, parsed)
        results[feed.id] = _write_articles(feed, parsed, db)

    db.commit()
    return results
