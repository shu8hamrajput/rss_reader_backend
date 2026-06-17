import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.routers.search import _probe_feed, _sniff_feed_type

from .conftest import _FakeAsyncClient, _response


def _patch_client(fake_client):
    return patch("app.routers.search.httpx.AsyncClient", return_value=fake_client)


def test_search_feeds_feedly_success(client):
    data = {
        "results": [
            {
                "feedId": "feed/https://example.com/feed.xml",
                "title": "Example Feed",
                "description": "An example feed",
                "website": "https://example.com",
                "subscribers": 100,
                "language": "en",
                "coverUrl": "https://example.com/cover.png",
                "velocity": 1.5,
            }
        ],
        "related": ["query one", "query two"],
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["feed_url"] == "https://example.com/feed.xml"
    assert body["results"][0]["title"] == "Example Feed"
    assert body["related_queries"] == ["query one", "query two"]


def test_search_feeds_feedly_http_status_error(client):
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust"})

    assert resp.status_code == 502


def test_search_feeds_feedly_request_error(client):
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust"})

    assert resp.status_code == 502


def test_search_feeds_podcast_index_missing_credentials(client, monkeypatch):
    monkeypatch.delenv("PODCAST_INDEX_KEY", raising=False)
    monkeypatch.delenv("PODCAST_INDEX_SECRET", raising=False)

    resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "podcast_index"})
    assert resp.status_code == 503


def test_search_feeds_itunes_success(client):
    data = {
        "results": [
            {
                "feedUrl": "https://example.com/podcast.xml",
                "collectionName": "Example Podcast",
                "artistName": "Example Artist",
                "collectionViewUrl": "https://podcasts.apple.com/example",
                "trackCount": 42,
                "primaryGenreName": "Technology",
                "artworkUrl600": "https://example.com/art600.png",
            }
        ]
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "itunes"})

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["feed_url"] == "https://example.com/podcast.xml"
    assert result["title"] == "Example Podcast"
    assert result["description"] == "Example Artist"
    assert result["cover_url"] == "https://example.com/art600.png"


def test_search_feeds_bogus_source_returns_422(client):
    resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "bogus"})
    assert resp.status_code == 422


def test_discover_feeds_via_link_tag(client):
    html = (
        '<html><head>'
        '<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed.xml">'
        '</head><body></body></html>'
    )
    page_resp = _response(text=html, headers={"content-type": "text/html"}, url="https://example.com/")
    fake = _FakeAsyncClient(get=AsyncMock(return_value=page_resp))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/discover", params={"url": "https://example.com"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["feeds"] == [{"feed_url": "https://example.com/feed.xml", "title": "Feed", "feed_type": "rss"}]


def test_discover_feeds_invalid_scheme_returns_422(client):
    resp = client.get("/api/v1/search/discover", params={"url": "ftp://example.com"})
    assert resp.status_code == 422


def test_discover_feeds_rejects_private_address(client):
    resp = client.get("/api/v1/search/discover", params={"url": "http://169.254.169.254/latest/meta-data"})
    assert resp.status_code == 422


def test_search_feeds_feedly_skips_results_without_feed_id(client):
    data = {
        "results": [
            {"feedId": "", "title": "No Id"},
            {"feedId": "feed/https://example.com/feed.xml", "title": "Has Id"},
        ],
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["feed_url"] == "https://example.com/feed.xml"


def test_search_feeds_podcast_index_success(client, monkeypatch):
    monkeypatch.setenv("PODCAST_INDEX_KEY", "key")
    monkeypatch.setenv("PODCAST_INDEX_SECRET", "secret")
    data = {
        "feeds": [
            {
                "url": "https://example.com/podcast.xml",
                "title": "Pod",
                "description": "Desc",
                "link": "https://example.com",
                "episodeCount": 10,
                "language": "en",
                "image": "https://example.com/art.png",
            },
            {"title": "No URL"},
        ],
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "podcast_index"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["feed_url"] == "https://example.com/podcast.xml"
    assert results[0]["website_url"] == "https://example.com"


def test_search_feeds_podcast_index_http_status_error(client, monkeypatch):
    monkeypatch.setenv("PODCAST_INDEX_KEY", "key")
    monkeypatch.setenv("PODCAST_INDEX_SECRET", "secret")
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "podcast_index"})

    assert resp.status_code == 502


def test_search_feeds_podcast_index_request_error(client, monkeypatch):
    monkeypatch.setenv("PODCAST_INDEX_KEY", "key")
    monkeypatch.setenv("PODCAST_INDEX_SECRET", "secret")
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "podcast_index"})

    assert resp.status_code == 502


def test_search_feeds_itunes_http_status_error(client):
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "itunes"})

    assert resp.status_code == 502


def test_search_feeds_itunes_request_error(client):
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "itunes"})

    assert resp.status_code == 502


def test_search_feeds_gpodder_success(client):
    data = [
        {
            "url": "https://example.com/feed.xml",
            "title": "Gpodder Feed",
            "description": "Desc",
            "website": "https://example.com",
            "subscribers": 5,
            "logo_url": "https://example.com/logo.png",
        },
        {"title": "No URL"},
    ]
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "gpodder"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["feed_url"] == "https://example.com/feed.xml"
    assert results[0]["cover_url"] == "https://example.com/logo.png"


def test_search_feeds_gpodder_non_list_response_returns_empty(client):
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data={"error": "nope"})))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "gpodder"})

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_feeds_gpodder_http_status_error(client):
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "gpodder"})

    assert resp.status_code == 502


def test_search_feeds_gpodder_request_error(client):
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "gpodder"})

    assert resp.status_code == 502


def test_search_feeds_fyyd_success(client):
    data = {
        "data": [
            {
                "xmlURL": "https://example.com/feed.xml",
                "title": "Fyyd Feed",
                "description": "Desc",
                "htmlURL": "https://example.com",
                "episode_count": 7,
                "language": "en",
                "layoutImageURL": "https://example.com/cover.png",
            },
            {"title": "No xmlURL"},
        ],
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "fyyd"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["feed_url"] == "https://example.com/feed.xml"
    assert results[0]["cover_url"] == "https://example.com/cover.png"


def test_search_feeds_fyyd_non_list_data_returns_empty(client):
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data={"data": "nope"})))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "fyyd"})

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_feeds_fyyd_http_status_error(client):
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "fyyd"})

    assert resp.status_code == 502


def test_search_feeds_fyyd_request_error(client):
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "fyyd"})

    assert resp.status_code == 502


def test_discover_feeds_http_status_error(client):
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/discover", params={"url": "https://example.com"})

    assert resp.status_code == 502


def test_discover_feeds_request_error(client):
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/discover", params={"url": "https://example.com"})

    assert resp.status_code == 502


def test_discover_feeds_skips_non_feed_mime_and_empty_href(client):
    html = (
        '<html><head>'
        '<link rel="alternate" type="application/pdf" href="/doc.pdf">'
        '<link rel="alternate" type="application/rss+xml" href="">'
        '<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed.xml">'
        '</head><body></body></html>'
    )
    page_resp = _response(text=html, headers={"content-type": "text/html"}, url="https://example.com/")
    fake = _FakeAsyncClient(get=AsyncMock(return_value=page_resp))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/discover", params={"url": "https://example.com"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["feeds"] == [{"feed_url": "https://example.com/feed.xml", "title": "Feed", "feed_type": "rss"}]


# ── _probe_feed ────────────────────────────────────────────────────────────────

def test_probe_feed_skips_already_seen_url():
    client = _FakeAsyncClient()
    seen = {"https://example.com/feed.xml"}

    result = asyncio.run(_probe_feed(client, "https://example.com/feed.xml", seen))

    assert result is None
    client.head.assert_not_called()


def test_probe_feed_head_mime_match_returns_feed():
    fake = _FakeAsyncClient(head=AsyncMock(return_value=_response(headers={"content-type": "application/rss+xml"})))
    seen = set()

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed.xml", seen))

    assert result.feed_url == "https://example.com/feed.xml"
    assert result.feed_type == "rss"
    assert "https://example.com/feed.xml" in seen


def test_probe_feed_head_request_error_returns_none():
    fake = _FakeAsyncClient(head=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed.xml", set()))

    assert result is None


def test_probe_feed_head_bad_status_returns_none():
    fake = _FakeAsyncClient(head=AsyncMock(return_value=_response(status_code=404)))

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed.xml", set()))

    assert result is None


def test_probe_feed_get_sniffs_feed_type():
    fake = _FakeAsyncClient(
        head=AsyncMock(return_value=_response(headers={"content-type": "text/html"})),
        get=AsyncMock(return_value=_response(text="<rss version='2.0'>", status_code=200)),
    )
    seen = set()

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed", seen))

    assert result.feed_url == "https://example.com/feed"
    assert result.feed_type == "rss"
    assert "https://example.com/feed" in seen


def test_probe_feed_get_request_error_returns_none():
    fake = _FakeAsyncClient(
        head=AsyncMock(return_value=_response(headers={"content-type": "text/html"})),
        get=AsyncMock(side_effect=httpx.RequestError("connection failed")),
    )

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed", set()))

    assert result is None


def test_probe_feed_get_non_200_returns_none():
    fake = _FakeAsyncClient(
        head=AsyncMock(return_value=_response(headers={"content-type": "text/html"})),
        get=AsyncMock(return_value=_response(text="<rss>", status_code=404)),
    )

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed", set()))

    assert result is None


def test_probe_feed_get_no_feed_markers_returns_none():
    fake = _FakeAsyncClient(
        head=AsyncMock(return_value=_response(headers={"content-type": "text/html"})),
        get=AsyncMock(return_value=_response(text="<html>nope</html>", status_code=200)),
    )

    result = asyncio.run(_probe_feed(fake, "https://example.com/feed", set()))

    assert result is None


# ── _sniff_feed_type ─────────────────────────────────────────────────────────

def test_sniff_feed_type_rss():
    assert _sniff_feed_type("<rss version='2.0'><channel></channel></rss>") == "rss"


def test_sniff_feed_type_atom():
    assert _sniff_feed_type("<feed xmlns='http://www.w3.org/2005/Atom'></feed>") == "atom"


def test_sniff_feed_type_json():
    assert _sniff_feed_type('{"version": "https://jsonfeed.org/version/1"}') == "json"


def test_sniff_feed_type_none_for_unrelated_content():
    assert _sniff_feed_type("<html><body>hello</body></html>") is None


def test_search_feeds_youtube_missing_credentials(client, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "youtube"})
    assert resp.status_code == 503


def test_search_feeds_youtube_success(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    data = {
        "items": [
            {
                "id": {"channelId": "UC123"},
                "snippet": {
                    "title": "Example Channel",
                    "description": "Desc",
                    "thumbnails": {"high": {"url": "https://example.com/thumb.jpg"}},
                },
            },
            {"id": {}, "snippet": {"title": "No Channel Id"}},
        ],
    }
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(json_data=data)))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "youtube"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["feed_url"] == "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
    assert results[0]["title"] == "Example Channel"
    assert results[0]["cover_url"] == "https://example.com/thumb.jpg"
    assert results[0]["website_url"] == "https://www.youtube.com/channel/UC123"


def test_search_feeds_youtube_http_status_error(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    error_response = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_response),
    )))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "youtube"})

    assert resp.status_code == 502


def test_search_feeds_youtube_request_error(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    fake = _FakeAsyncClient(get=AsyncMock(side_effect=httpx.RequestError("connection failed")))

    with _patch_client(fake):
        resp = client.get("/api/v1/search/feeds", params={"q": "rust", "source": "youtube"})

    assert resp.status_code == 502


def test_discover_feeds_falls_back_to_common_path_probing(client):
    html = "<html><head></head><body>no feed links here</body></html>"
    page_resp = _response(text=html, headers={"content-type": "text/html"}, url="https://example.com/")

    def _head_side_effect(url, **kwargs):
        if url == "https://example.com/feed.xml":
            return _response(headers={"content-type": "application/rss+xml"})
        return _response(status_code=404)

    fake = _FakeAsyncClient(
        get=AsyncMock(return_value=page_resp),
        head=AsyncMock(side_effect=_head_side_effect),
    )

    with _patch_client(fake):
        resp = client.get("/api/v1/search/discover", params={"url": "https://example.com"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["feeds"] == [{"feed_url": "https://example.com/feed.xml", "title": None, "feed_type": "rss"}]
