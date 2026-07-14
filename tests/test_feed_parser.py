import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import feedparser
import httpx
import pytest

from app.models import Article
from app.plugins.base import ParsedArticle, ParsedFeed
from app.plugins.default import _get_content, _get_thumbnail, _parse_date
from app.services.feed_parser import (
    _apply_feed_meta,
    _write_articles,
    refresh_feed,
    refresh_url_for_all_subscribers,
)

from .conftest import _FakeAsyncClient, _response, make_article, make_feed

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <link>https://example.com</link>
  <description>A test feed</description>
  <item>
    <title>Existing Article</title>
    <link>https://example.com/existing</link>
    <guid>existing-guid</guid>
    <description>Existing summary</description>
  </item>
  <item>
    <title>New Article</title>
    <link>https://example.com/new</link>
    <guid>new-guid</guid>
    <description>New summary</description>
  </item>
</channel>
</rss>"""

RSS_XML_SINGLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Shared Feed</title>
  <link>https://example.com</link>
  <description>A shared feed</description>
  <item>
    <title>Shared Item</title>
    <link>https://example.com/shared-item</link>
    <guid>shared-item-1</guid>
    <description>Shared summary</description>
  </item>
</channel>
</rss>"""


# ── refresh_feed (dispatches to DefaultPlugin for plain example.com URLs) ──────

def test_refresh_feed_not_modified(db_session, user):
    feed = make_feed(db_session, user, etag="old-etag", last_modified="old-lm")
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=304)))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake):
        new_count = asyncio.run(refresh_feed(feed, db_session))

    assert new_count == 0
    assert feed.last_fetched_at is not None
    assert feed.etag == "old-etag"
    assert feed.last_modified == "old-lm"


def test_refresh_feed_force_bypasses_conditional_headers(db_session, user):
    """Manual refresh (force=True) must not send If-None-Match/If-Modified-Since —
    otherwise hosts that echo back a stale ETag/Last-Modified would make every
    user-initiated refresh silently return 0 new articles forever."""
    feed = make_feed(db_session, user, etag="old-etag", last_modified="old-lm")
    make_article(db_session, feed, guid="existing-guid")

    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        status_code=200, text=RSS_XML,
        headers={"ETag": "etag123", "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
    )))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake), \
            patch("app.enrichers.full_content.fetch_full_content", new=AsyncMock(return_value=None)):
        new_count = asyncio.run(refresh_feed(feed, db_session, force=True))

    sent_headers = fake.get.call_args.kwargs["headers"]
    assert "If-None-Match" not in sent_headers
    assert "If-Modified-Since" not in sent_headers
    assert new_count == 1
    assert feed.etag == "etag123"


def test_refresh_feed_inserts_only_new_articles(db_session, user):
    feed = make_feed(db_session, user, title=None, etag=None, last_modified=None)
    make_article(db_session, feed, guid="existing-guid")

    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        status_code=200, text=RSS_XML,
        headers={"ETag": "etag123", "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
    )))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake), \
            patch("app.enrichers.full_content.fetch_full_content", new=AsyncMock(return_value=None)):
        new_count = asyncio.run(refresh_feed(feed, db_session))

    assert new_count == 1
    assert feed.etag == "etag123"
    assert feed.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert feed.title == "Test Feed"
    assert feed.description == "A test feed"
    assert feed.site_url == "https://example.com"

    guids = {a.guid for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert guids == {"existing-guid", "new-guid"}


def test_refresh_feed_rejects_private_address(db_session, user):
    feed = make_feed(db_session, user, url="http://169.254.169.254/latest/meta-data")

    with pytest.raises(ValueError):
        asyncio.run(refresh_feed(feed, db_session))


def test_refresh_feed_propagates_http_errors(db_session, user):
    feed = make_feed(db_session, user)
    error_resp = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        status_code=500,
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_resp),
    )))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(refresh_feed(feed, db_session))


def test_refresh_feed_bozo_with_no_entries_raises(db_session, user):
    feed = make_feed(db_session, user)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=200, text="not xml at all", headers={})))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake):
        with pytest.raises(ValueError):
            asyncio.run(refresh_feed(feed, db_session))


# ── _write_articles ──────────────────────────────────────────────────────────

def test_write_articles_skips_existing_guids(db_session, user):
    feed = make_feed(db_session, user)
    make_article(db_session, feed, guid="existing-guid")

    parsed = ParsedFeed(articles=[
        ParsedArticle(guid="existing-guid", title="Should be skipped"),
        ParsedArticle(guid="new-guid", title="New Article", duration_seconds=42),
    ])

    new_count = _write_articles(feed, parsed, db_session)
    db_session.commit()

    assert new_count == 1
    guids = {a.guid for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert guids == {"existing-guid", "new-guid"}


def test_write_articles_dedupes_within_same_batch(db_session, user):
    feed = make_feed(db_session, user)
    parsed = ParsedFeed(articles=[
        ParsedArticle(guid="dup", title="First"),
        ParsedArticle(guid="dup", title="Second"),
    ])

    new_count = _write_articles(feed, parsed, db_session)
    db_session.commit()

    assert new_count == 1


def test_write_articles_defaults_to_unread(db_session, user):
    feed = make_feed(db_session, user, auto_mark_read=False)
    parsed = ParsedFeed(articles=[ParsedArticle(guid="g1", title="Article")])

    _write_articles(feed, parsed, db_session)
    db_session.commit()

    article = db_session.query(Article).filter(Article.feed_id == feed.id, Article.guid == "g1").one()
    assert article.is_read is False
    assert article.read_at is None


def test_write_articles_auto_mark_read_marks_new_articles_read(db_session, user):
    feed = make_feed(db_session, user, auto_mark_read=True)
    parsed = ParsedFeed(articles=[ParsedArticle(guid="g1", title="Article")])

    _write_articles(feed, parsed, db_session)
    db_session.commit()

    article = db_session.query(Article).filter(Article.feed_id == feed.id, Article.guid == "g1").one()
    assert article.is_read is True
    assert article.read_at is not None


# ── _apply_feed_meta ─────────────────────────────────────────────────────────

def test_apply_feed_meta_sets_title_only_when_absent(db_session, user):
    feed = make_feed(db_session, user, title="Existing Title")
    parsed = ParsedFeed(title="New Title", description="New description", site_url="https://e.com", icon_url="https://e.com/icon.png")

    _apply_feed_meta(feed, parsed, "default")

    assert feed.title == "Existing Title"  # not overwritten
    assert feed.description == "New description"
    assert feed.site_url == "https://e.com"
    assert feed.icon_url == "https://e.com/icon.png"
    assert feed.plugin_name == "default"


def test_apply_feed_meta_fills_missing_title(db_session, user):
    feed = make_feed(db_session, user, title=None)
    parsed = ParsedFeed(title="Fetched Title")

    _apply_feed_meta(feed, parsed, "default")

    assert feed.title == "Fetched Title"


def test_apply_feed_meta_does_not_overwrite_existing_plugin_name(db_session, user):
    feed = make_feed(db_session, user, plugin_name="youtube")
    parsed = ParsedFeed(title="X")

    _apply_feed_meta(feed, parsed, "default")

    assert feed.plugin_name == "youtube"


# ── _parse_date / _get_content / _get_thumbnail (DefaultPlugin helpers) ───────

def test_parse_date_with_published_parsed():
    entry = feedparser.FeedParserDict({"published_parsed": time.struct_time((2025, 1, 2, 10, 0, 0, 3, 2, 0))})
    assert _parse_date(entry) == datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_date_falls_back_to_string_published():
    entry = feedparser.FeedParserDict({"published": "Wed, 02 Jan 2025 10:00:00 GMT"})
    assert _parse_date(entry) == datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_date_returns_none_when_absent():
    assert _parse_date(feedparser.FeedParserDict({})) is None


def test_get_content_prefers_content_over_summary():
    entry = feedparser.FeedParserDict({"content": [{"value": "Full content"}], "summary": "Summary"})
    assert _get_content(entry) == "Full content"


def test_get_content_falls_back_to_summary():
    entry = {"summary": "Summary only"}
    assert _get_content(entry) == "Summary only"


def test_get_thumbnail_prefers_media_thumbnail():
    entry = {"media_thumbnail": [{"url": "https://e.com/t.jpg"}]}
    assert _get_thumbnail(entry) == "https://e.com/t.jpg"


def test_get_thumbnail_falls_back_to_image_enclosure():
    entry = {"enclosures": [{"type": "image/png", "href": "https://e.com/e.png"}]}
    assert _get_thumbnail(entry) == "https://e.com/e.png"


def test_get_thumbnail_none_when_no_image_source():
    entry = {"enclosures": [{"type": "audio/mpeg", "href": "https://e.com/a.mp3"}]}
    assert _get_thumbnail(entry) is None


# ── refresh_url_for_all_subscribers ──────────────────────────────────────────

def test_refresh_url_for_all_subscribers_fetches_once_for_shared_url(db_session, user, other_user):
    feed1 = make_feed(db_session, user, url="https://example.com/shared.xml", title=None)
    feed2 = make_feed(db_session, other_user, url="https://example.com/shared.xml", title=None)

    fake_get = AsyncMock(return_value=_response(status_code=200, text=RSS_XML_SINGLE, headers={}))
    fake = _FakeAsyncClient(get=fake_get)

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake), \
            patch("app.enrichers.full_content.fetch_full_content", new=AsyncMock(return_value=None)):
        results = asyncio.run(refresh_url_for_all_subscribers([feed1, feed2], db_session))

    fake_get.assert_called_once()
    assert results == {feed1.id: 1, feed2.id: 1}
    assert feed1.last_fetched_at is not None
    assert feed2.last_fetched_at is not None


def test_refresh_url_for_all_subscribers_not_modified(db_session, user, other_user):
    feed1 = make_feed(db_session, user, url="https://example.com/shared2.xml")
    feed2 = make_feed(db_session, other_user, url="https://example.com/shared2.xml")

    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=304)))

    with patch("app.plugins.default.httpx.AsyncClient", return_value=fake):
        results = asyncio.run(refresh_url_for_all_subscribers([feed1, feed2], db_session))

    assert results == {feed1.id: 0, feed2.id: 0}
    assert feed1.last_fetched_at is not None
    assert feed2.last_fetched_at is not None
