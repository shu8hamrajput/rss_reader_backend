import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import feedparser
import httpx
import pytest

from app.models import Article
from app.services.feed_parser import (
    _apply_parsed_to_feed,
    _get_content,
    _get_thumbnail,
    _parse_date,
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


# ── refresh_feed ──────────────────────────────────────────────────────────────

def test_refresh_feed_not_modified(db_session, user):
    feed = make_feed(db_session, user, etag="old-etag", last_modified="old-lm")
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=304)))

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake):
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

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake), \
            patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
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

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake), \
            patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
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

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(refresh_feed(feed, db_session))


def test_refresh_feed_bozo_with_no_entries_raises(db_session, user):
    feed = make_feed(db_session, user)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=200, text="not xml at all", headers={})))

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake):
        with pytest.raises(ValueError):
            asyncio.run(refresh_feed(feed, db_session))


# ── _apply_parsed_to_feed ─────────────────────────────────────────────────────

def test_apply_parsed_to_feed_computes_itunes_duration(db_session, user):
    feed = make_feed(db_session, user)
    parsed = feedparser.FeedParserDict({
        "feed": feedparser.FeedParserDict({"title": "Podcast Feed"}),
        "entries": [
            {"id": "g1", "title": "Ep1", "link": "https://e.com/1", "summary": "s1", "itunes_duration": "1:02:03"},
            {"id": "g2", "title": "Ep2", "link": "https://e.com/2", "summary": "s2", "itunes_duration": "12:34"},
            {"id": "g3", "title": "Ep3", "link": "https://e.com/3", "summary": "s3", "itunes_duration": "45"},
        ],
    })

    with patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
        new_count = asyncio.run(_apply_parsed_to_feed(feed, parsed, None, None, db_session))
    db_session.commit()

    assert new_count == 3
    durations = {a.guid: a.duration_seconds for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert durations == {"g1": 3723, "g2": 754, "g3": 45}


def test_apply_parsed_to_feed_thumbnail_resolution(db_session, user):
    feed = make_feed(db_session, user)
    parsed = feedparser.FeedParserDict({
        "feed": feedparser.FeedParserDict({"title": "Feed"}),
        "entries": [
            {
                "id": "media-thumb", "title": "A", "link": "https://e.com/a", "summary": "sa",
                "media_thumbnail": [{"url": "https://e.com/a-thumb.jpg"}],
                "enclosures": [{"type": "image/png", "href": "https://e.com/a-enc.png"}],
            },
            {
                "id": "image-enclosure", "title": "B", "link": "https://e.com/b", "summary": "sb",
                "enclosures": [{"type": "image/jpeg", "href": "https://e.com/b-enc.jpg"}],
            },
            {
                "id": "no-image", "title": "C", "link": "https://e.com/c", "summary": "sc",
                "enclosures": [{"type": "audio/mpeg", "href": "https://e.com/c.mp3"}],
            },
        ],
    })

    with patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
        asyncio.run(_apply_parsed_to_feed(feed, parsed, None, None, db_session))
    db_session.commit()

    thumbnails = {a.guid: a.thumbnail_url for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert thumbnails["media-thumb"] == "https://e.com/a-thumb.jpg"
    assert thumbnails["image-enclosure"] == "https://e.com/b-enc.jpg"
    assert thumbnails["no-image"] is None


def test_apply_parsed_to_feed_fetches_transcript_only_for_audio_without_full_content(db_session, user):
    feed = make_feed(db_session, user)
    parsed = feedparser.FeedParserDict({
        "feed": feedparser.FeedParserDict({"title": "Feed"}),
        "entries": [
            {
                "id": "audio-with-transcript", "title": "D", "link": "https://e.com/d", "summary": "sd",
                "enclosures": [{"type": "audio/mpeg", "href": "https://e.com/d.mp3"}],
                "podcast_transcript": [{"url": "https://e.com/d.vtt"}],
            },
            {
                "id": "non-audio-with-transcript", "title": "E", "link": "https://e.com/e", "summary": "se",
                "enclosures": [{"type": "image/png", "href": "https://e.com/e.png"}],
                "podcast_transcript": [{"url": "https://e.com/e.vtt"}],
            },
            {
                "id": "audio-with-existing-content", "title": "F", "link": "https://e.com/f", "summary": "sf",
                "enclosures": [{"type": "audio/mpeg", "href": "https://e.com/f.mp3"}],
                "podcast_transcript": [{"url": "https://e.com/f.vtt"}],
            },
        ],
    })

    async def _fake_fetch_full_content(url):
        return "Already fetched full content" if url == "https://e.com/f" else None

    with patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(side_effect=_fake_fetch_full_content)), \
            patch("app.services.feed_parser._fetch_transcript", new=AsyncMock(return_value="transcript text")) as mock_transcript:
        asyncio.run(_apply_parsed_to_feed(feed, parsed, None, None, db_session))
    db_session.commit()

    mock_transcript.assert_called_once_with("https://e.com/d.vtt")


def test_apply_parsed_to_feed_marks_youtube_channel_entries_as_playable(db_session, user):
    feed = make_feed(db_session, user, url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
    parsed = feedparser.FeedParserDict({
        "feed": feedparser.FeedParserDict({"title": "Some Channel"}),
        "entries": [
            {
                "id": "yt:video:abc123", "title": "Episode 1",
                "link": "https://www.youtube.com/watch?v=abc123",
                "summary": "s1", "author": "Some Channel",
                "media_thumbnail": [{"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"}],
            },
            {
                "id": "yt:video:short1", "title": "Short 1",
                "link": "https://www.youtube.com/shorts/short1",
                "summary": "s2", "author": "Some Channel",
            },
        ],
    })

    with patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
        asyncio.run(_apply_parsed_to_feed(feed, parsed, None, None, db_session))
    db_session.commit()

    articles = {a.guid: a for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert articles["yt:video:abc123"].media_type == "video/youtube"
    assert articles["yt:video:abc123"].media_url == "https://www.youtube.com/watch?v=abc123"
    assert articles["yt:video:abc123"].thumbnail_url == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    assert articles["yt:video:abc123"].itunes_author == "Some Channel"
    assert articles["yt:video:short1"].media_type == "video/youtube"
    assert articles["yt:video:short1"].media_url == "https://www.youtube.com/shorts/short1"


def test_apply_parsed_to_feed_non_youtube_feed_unaffected(db_session, user):
    feed = make_feed(db_session, user)  # default non-YouTube url
    parsed = feedparser.FeedParserDict({
        "feed": feedparser.FeedParserDict({"title": "Feed"}),
        "entries": [
            {"id": "plain", "title": "Plain article", "link": "https://example.com/a", "summary": "s"},
        ],
    })

    with patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
        asyncio.run(_apply_parsed_to_feed(feed, parsed, None, None, db_session))
    db_session.commit()

    article = db_session.query(Article).filter(Article.feed_id == feed.id, Article.guid == "plain").one()
    assert article.media_type is None
    assert article.media_url is None


# ── _parse_date ───────────────────────────────────────────────────────────────

def test_parse_date_with_published_parsed():
    entry = feedparser.FeedParserDict({"published_parsed": time.struct_time((2025, 1, 2, 10, 0, 0, 3, 2, 0))})
    assert _parse_date(entry) == datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_date_falls_back_to_string_published():
    entry = feedparser.FeedParserDict({"published": "Wed, 02 Jan 2025 10:00:00 GMT"})
    assert _parse_date(entry) == datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_date_returns_none_when_absent():
    assert _parse_date(feedparser.FeedParserDict({})) is None


# ── _get_content / _get_thumbnail ────────────────────────────────────────────

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

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake), \
            patch("app.services.feed_parser.fetch_full_content", new=AsyncMock(return_value=None)):
        results = asyncio.run(refresh_url_for_all_subscribers([feed1, feed2], db_session))

    fake_get.assert_called_once()
    assert results == {feed1.id: 1, feed2.id: 1}
    assert feed1.last_fetched_at is not None
    assert feed2.last_fetched_at is not None


def test_refresh_url_for_all_subscribers_not_modified(db_session, user, other_user):
    feed1 = make_feed(db_session, user, url="https://example.com/shared2.xml")
    feed2 = make_feed(db_session, other_user, url="https://example.com/shared2.xml")

    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(status_code=304)))

    with patch("app.services.feed_parser.httpx.AsyncClient", return_value=fake):
        results = asyncio.run(refresh_url_for_all_subscribers([feed1, feed2], db_session))

    assert results == {feed1.id: 0, feed2.id: 0}
    assert feed1.last_fetched_at is not None
    assert feed2.last_fetched_at is not None
