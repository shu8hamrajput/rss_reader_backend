from unittest.mock import AsyncMock, MagicMock, patch

import httpx

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
