"""
Feed parser — orchestration layer: plugin dispatch → enricher pipeline → DB write.

Flow:
  1. plugin_registry.get_fetch_plugin(url).fetch()  → ParsedFeed (raw)
  2. enricher_registry.run(articles, plugin_name)   → ParsedFeed (enriched)
  3. _write_articles()                              → DB rows

Adding enrichment (AI tagging, translation, etc.) → app/enrichers/, no changes here.
Adding a feed type                                 → app/plugins/,   no changes here.

See ADR-001, ADR-002.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from ..enrichers import enricher_registry
from ..models import Article, Feed
from ..plugins import plugin_registry
from ..plugins.base import ParsedFeed

logger = logging.getLogger(__name__)

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "igsh", "ref_src", "_hsenc", "_hsmi", "mkt_tok", "vero_id",
    "yclid", "ocid", "wt_zmc", "spm",
}


def _strip_tracking_params(url: str | None) -> str | None:
    """Strip known analytics/tracking query params, leaving the rest of the URL untouched."""
    if not url:
        return url
    split = urlsplit(url)
    if not split.query:
        return url
    kept = [
        (k, v) for k, v in parse_qsl(split.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PARAM_PREFIXES) and k.lower() not in _TRACKING_PARAMS
    ]
    if len(kept) == len(parse_qsl(split.query, keep_blank_values=True)):
        return url
    return urlunsplit(split._replace(query=urlencode(kept)))


def _apply_feed_meta(feed: Feed, parsed: ParsedFeed, plugin_name: str) -> None:
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
    if parsed.icon_url and not feed.icon_locked:
        feed.icon_url = parsed.icon_url
    if not feed.plugin_name:
        feed.plugin_name = plugin_name


def _write_articles(feed: Feed, parsed: ParsedFeed, db: Session) -> int:
    existing_guids: set[str] = {
        row[0] for row in db.query(Article.guid).filter(Article.feed_id == feed.id).all()
    }
    # Feeds with auto_mark_read skip the unread inbox entirely — for low-signal
    # feeds skimmed via smart views but never opened individually.
    read_at = datetime.now(timezone.utc) if feed.auto_mark_read else None
    new_count = 0
    for art in parsed.articles:
        if not art.guid or art.guid in existing_guids:
            continue
        db.add(Article(
            feed_id=feed.id, guid=art.guid, title=art.title, url=_strip_tracking_params(art.url),
            author=art.author, summary=art.summary, content=art.content,
            full_content=art.full_content, thumbnail_url=art.thumbnail_url,
            published_at=art.published_at, media_type=art.media_type,
            media_url=art.media_url, duration_seconds=art.duration_seconds,
            episode_number=art.episode_number, itunes_author=art.itunes_author,
            tags=json.dumps(art.tags) if art.tags else None,
            is_read=feed.auto_mark_read, read_at=read_at,
        ))
        existing_guids.add(art.guid)
        new_count += 1
    return new_count


async def refresh_feed(feed: Feed, db: Session, force: bool = False) -> int:
    """Fetch the feed, store new articles, update HTTP cache headers. Returns new article count.

    `force=True` skips the If-None-Match/If-Modified-Since conditional-GET headers,
    bypassing any 304 the origin would otherwise return. Manual, user-initiated
    refreshes use this — some feed hosts (WordPress + CDN combos in particular)
    echo back a stale ETag/Last-Modified even when new entries exist, which would
    otherwise make every subsequent manual refresh silently no-op forever.
    """
    plugin = plugin_registry.get_fetch_plugin(feed.url)
    parsed, _ = await plugin.fetch(feed.url, feed.etag, feed.last_modified, force=force)
    feed.last_fetched_at = datetime.now(timezone.utc)

    if parsed is None:
        db.commit()
        return 0

    if parsed.articles:
        parsed.articles = await enricher_registry.run(parsed.articles, plugin.name)

    _apply_feed_meta(feed, parsed, plugin.name)
    new_count = _write_articles(feed, parsed, db)
    db.commit()
    return new_count


async def refresh_url_for_all_subscribers(feeds: list[Feed], db: Session) -> dict[int, int]:
    if not feeds:
        return {}

    _EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
    reference = max(feeds, key=lambda f: f.last_fetched_at or _EPOCH)
    plugin = plugin_registry.get_fetch_plugin(feeds[0].url)
    parsed, _ = await plugin.fetch(feeds[0].url, reference.etag, reference.last_modified)

    now = datetime.now(timezone.utc)
    if parsed is None:
        for f in feeds:
            f.last_fetched_at = now
        db.commit()
        return {f.id: 0 for f in feeds}

    if parsed.articles:
        parsed.articles = await enricher_registry.run(parsed.articles, plugin.name)

    results: dict[int, int] = {}
    for feed in feeds:
        feed.last_fetched_at = now
        _apply_feed_meta(feed, parsed, plugin.name)
        results[feed.id] = _write_articles(feed, parsed, db)

    db.commit()
    return results
