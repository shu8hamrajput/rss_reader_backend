import json
from unittest.mock import AsyncMock, patch

from .conftest import make_article, make_feed


def test_list_articles_pagination(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    for _ in range(3):
        make_article(db_session, feed)

    resp = client.get("/api/v1/articles", params={"page": 1, "page_size": 2}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    resp = client.get("/api/v1/articles", params={"page": 2, "page_size": 2}, headers=auth_headers)
    body = resp.json()
    assert len(body["items"]) == 1


def test_list_articles_feed_id_filter(client, db_session, user, auth_headers):
    feed1 = make_feed(db_session, user)
    feed2 = make_feed(db_session, user)
    a1 = make_article(db_session, feed1)
    make_article(db_session, feed2)

    resp = client.get("/api/v1/articles", params={"feed_id": feed1.id}, headers=auth_headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == a1.id


def test_list_articles_is_read_and_bookmarked_filters(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    unread = make_article(db_session, feed, is_read=False)
    read_bookmarked = make_article(db_session, feed, is_read=True, is_bookmarked=True)

    resp = client.get("/api/v1/articles", params={"is_read": True}, headers=auth_headers)
    ids = [a["id"] for a in resp.json()["items"]]
    assert ids == [read_bookmarked.id]

    resp = client.get("/api/v1/articles", params={"is_read": False}, headers=auth_headers)
    ids = [a["id"] for a in resp.json()["items"]]
    assert ids == [unread.id]

    resp = client.get("/api/v1/articles", params={"is_bookmarked": True}, headers=auth_headers)
    ids = [a["id"] for a in resp.json()["items"]]
    assert ids == [read_bookmarked.id]


def test_list_articles_tag_filter(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    tagged = make_article(db_session, feed, tags=json.dumps(["read_later"]))
    make_article(db_session, feed)

    resp = client.get("/api/v1/articles", params={"tag": "read_later"}, headers=auth_headers)
    ids = [a["id"] for a in resp.json()["items"]]
    assert ids == [tagged.id]


def test_list_articles_has_audio_filter(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    audio = make_article(db_session, feed, media_type="audio/mpeg")
    make_article(db_session, feed)

    resp = client.get("/api/v1/articles", params={"has_audio": True}, headers=auth_headers)
    ids = [a["id"] for a in resp.json()["items"]]
    assert ids == [audio.id]


def test_list_articles_search(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    target = make_article(db_session, feed, title="Quokka population rebounds in Western Australia")
    make_article(db_session, feed, title="Unrelated headline about something else")

    resp = client.get("/api/v1/articles", params={"search": "Quokka"}, headers=auth_headers)
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()["items"]]
    assert target.id in ids


def test_get_article_not_owned_returns_404(client, db_session, other_user, auth_headers):
    feed = make_feed(db_session, other_user)
    article = make_article(db_session, feed)

    resp = client.get(f"/api/v1/articles/{article.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_update_read_status(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.patch(f"/api/v1/articles/{article.id}/read", json={"is_read": True}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_read"] is True
    assert body["created_at"]

    resp = client.patch(f"/api/v1/articles/{article.id}/read", json={"is_read": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_read"] is False


def test_update_bookmark_status(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.patch(f"/api/v1/articles/{article.id}/bookmark", json={"is_bookmarked": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_bookmarked"] is True


def test_mark_all_read(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    make_article(db_session, feed, is_read=False)
    make_article(db_session, feed, is_read=False)

    resp = client.post(f"/api/v1/articles/feeds/{feed.id}/mark-all-read", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/articles", params={"feed_id": feed.id}, headers=auth_headers)
    assert all(a["is_read"] for a in resp.json()["items"])


def test_bulk_mark_read_toggles_system_tags(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    a1 = make_article(db_session, feed)
    a2 = make_article(db_session, feed)

    resp = client.post(
        "/api/v1/articles/bulk/mark-read",
        json={"article_ids": [a1.id, a2.id], "is_read": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2

    article = client.get(f"/api/v1/articles/{a1.id}", headers=auth_headers).json()
    assert article["is_read"] is True
    assert "read" in article["tags"]
    assert "unread" not in article["tags"]

    resp = client.post(
        "/api/v1/articles/bulk/mark-read",
        json={"article_ids": [a1.id, a2.id], "is_read": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    article = client.get(f"/api/v1/articles/{a1.id}", headers=auth_headers).json()
    assert article["is_read"] is False
    assert "unread" in article["tags"]
    assert "read" not in article["tags"]


def test_bulk_bookmark(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    a1 = make_article(db_session, feed)
    a2 = make_article(db_session, feed)

    resp = client.post(
        "/api/v1/articles/bulk/bookmark",
        json={"article_ids": [a1.id, a2.id], "is_bookmarked": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2


def test_bulk_read_later_add_and_remove(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.post(
        "/api/v1/articles/bulk/read-later",
        json={"article_ids": [article.id], "value": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = client.get(f"/api/v1/articles/{article.id}", headers=auth_headers).json()
    assert "read_later" in body["tags"]

    resp = client.post(
        "/api/v1/articles/bulk/read-later",
        json={"article_ids": [article.id], "value": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = client.get(f"/api/v1/articles/{article.id}", headers=auth_headers).json()
    assert "read_later" not in body["tags"]


def test_user_tags_excludes_system_tags(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    make_article(db_session, feed, tags=json.dumps(["read_later", "custom_tag"]))

    resp = client.get("/api/v1/articles/user-tags", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["custom_tag"]


def test_update_article_tags_preserves_system_tags(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, tags=json.dumps(["read_later"]))

    resp = client.patch(f"/api/v1/articles/{article.id}/tags", json={"tags": ["custom"]}, headers=auth_headers)
    assert resp.status_code == 200
    tags = resp.json()["tags"]
    assert "read_later" in tags
    assert "custom" in tags


def test_update_article_tags_filters_overlong_tags(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    long_tag = "x" * 100
    resp = client.patch(
        f"/api/v1/articles/{article.id}/tags",
        json={"tags": [long_tag, "short"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["short"]


def test_update_article_note(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.patch(f"/api/v1/articles/{article.id}/note", json={"note": "  remember this  "}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["article_note"] == "remember this"


def test_update_resume_position(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.patch(f"/api/v1/articles/{article.id}/resume", json={"resume_at_seconds": 120}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["resume_at_seconds"] == 120


def test_update_scroll_position_clamps(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.patch(f"/api/v1/articles/{article.id}/scroll", json={"scroll_pct": 150}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["scroll_pct"] == 100

    resp = client.patch(f"/api/v1/articles/{article.id}/scroll", json={"scroll_pct": -10}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["scroll_pct"] == 0


def test_reading_stats_shape(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    a1 = make_article(db_session, feed)
    a2 = make_article(db_session, feed)
    client.patch(f"/api/v1/articles/{a1.id}/read", json={"is_read": True}, headers=auth_headers)
    client.patch(f"/api/v1/articles/{a2.id}/read", json={"is_read": True}, headers=auth_headers)

    resp = client.get("/api/v1/articles/stats", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_articles"] == 2
    assert body["total_read"] == 2
    assert body["total_unread"] == 0
    assert len(body["daily_counts"]) == 30
    assert body["read_today"] == 2
    assert body["current_streak"] >= 1
    assert isinstance(body["top_feeds"], list)


def test_refetch_article(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url="https://example.com/full-article")

    with patch("app.routers.articles.fetch_full_content", new_callable=AsyncMock, return_value="<p>full text</p>"), \
         patch("app.routers.articles.remaining_fetch_quota", return_value=10), \
         patch("app.routers.articles.record_fetches") as mock_record:
        resp = client.post(f"/api/v1/articles/{article.id}/refetch", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["full_content"] == "<p>full text</p>"
    mock_record.assert_called_once_with(user, 1)


def test_refetch_article_quota_exceeded(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url="https://example.com/full-article")

    with patch("app.routers.articles.remaining_fetch_quota", return_value=0):
        resp = client.post(f"/api/v1/articles/{article.id}/refetch", headers=auth_headers)

    assert resp.status_code == 429


def test_refetch_article_without_url_returns_422(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url=None)

    resp = client.post(f"/api/v1/articles/{article.id}/refetch", headers=auth_headers)
    assert resp.status_code == 422


def test_request_parser(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url="https://www.example.com/articles/foo")

    resp = client.post(f"/api/v1/articles/{article.id}/request-parser", json={"note": "ads everywhere"}, headers=auth_headers)

    assert resp.status_code == 201
    body = resp.json()
    assert body["domain"] == "example.com"
    assert body["status"] == "pending"
    assert body["note"] == "ads everywhere"
    assert body["article_id"] == article.id


def test_request_parser_duplicate_pending_returns_existing(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url="https://example.com/articles/foo")
    other_article = make_article(db_session, feed, url="https://example.com/articles/bar")

    first = client.post(f"/api/v1/articles/{article.id}/request-parser", json={}, headers=auth_headers).json()
    second = client.post(f"/api/v1/articles/{other_article.id}/request-parser", json={}, headers=auth_headers).json()

    assert second["id"] == first["id"]


def test_request_parser_without_url_returns_422(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url=None)

    resp = client.post(f"/api/v1/articles/{article.id}/request-parser", json={}, headers=auth_headers)
    assert resp.status_code == 422


def test_bulk_save_later(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, url="https://example.com/save-me")

    with patch("app.routers.articles.fetch_full_content", new_callable=AsyncMock, return_value="<p>saved</p>"), \
         patch("app.routers.articles.remaining_fetch_quota", return_value=None), \
         patch("app.routers.articles.record_fetches") as mock_record:
        resp = client.post(
            "/api/v1/articles/bulk/save-later",
            json={"article_ids": [article.id], "value": True},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["fetched"] == 1
    mock_record.assert_called_once_with(user, 1)

    article_resp = client.get(f"/api/v1/articles/{article.id}", headers=auth_headers).json()
    assert "saved_later" in article_resp["tags"]
    assert article_resp["full_content"] == "<p>saved</p>"
