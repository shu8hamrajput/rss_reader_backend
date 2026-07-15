from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from .conftest import make_category, make_feed, make_article


def test_create_feed(client, auth_headers):
    with patch("app.routers.feeds.refresh_feed", new_callable=AsyncMock, return_value=0) as mock_refresh:
        resp = client.post(
            "/api/v1/feeds",
            json={"url": "https://example.com/feed.xml", "title": "Example Feed"},
            headers=auth_headers,
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://example.com/feed.xml"
    assert body["title"] == "Example Feed"
    assert body["categories"] == []
    assert body["article_count"] == 0
    assert body["unread_count"] == 0
    assert body["discovered_via"] == "manual"
    mock_refresh.assert_awaited_once()


def test_create_feed_with_discovered_via(client, auth_headers):
    with patch("app.routers.feeds.refresh_feed", new_callable=AsyncMock, return_value=0):
        resp = client.post(
            "/api/v1/feeds",
            json={"url": "https://example.com/found.xml", "discovered_via": "search"},
            headers=auth_headers,
        )
    assert resp.status_code == 201
    assert resp.json()["discovered_via"] == "search"


def test_create_feed_rejects_invalid_discovered_via(client, auth_headers):
    resp = client.post(
        "/api/v1/feeds",
        json={"url": "https://example.com/bad.xml", "discovered_via": "not_a_real_source"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_feed_duplicate_url_conflicts(client, db_session, user, auth_headers):
    make_feed(db_session, user, url="https://example.com/dup.xml")

    resp = client.post(
        "/api/v1/feeds",
        json={"url": "https://example.com/dup.xml"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_create_feed_plan_limit(client, db_session, user, auth_headers):
    for _ in range(25):
        make_feed(db_session, user)

    resp = client.post(
        "/api/v1/feeds",
        json={"url": "https://example.com/over-limit.xml"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_list_feeds_active_only_filter(client, db_session, user, auth_headers):
    make_feed(db_session, user, is_active=True)
    make_feed(db_session, user, is_active=False)

    resp = client.get("/api/v1/feeds", params={"active_only": True}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert all(f["is_active"] for f in body["items"])


def test_list_feeds_category_filter(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    in_cat = make_feed(db_session, user)
    make_feed(db_session, user)
    in_cat.categories.append(cat)
    db_session.commit()

    resp = client.get("/api/v1/feeds", params={"category_id": cat.id}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == in_cat.id


def test_get_feed_not_owned_returns_404(client, db_session, other_user, auth_headers):
    feed = make_feed(db_session, other_user)
    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_update_feed_title(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, title="Old Title")
    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"title": "New Title"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


def test_update_feed_category_reassignment(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    cat = make_category(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"category_ids": [cat.id]}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body["categories"]] == [cat.id]


def test_update_feed_category_not_owned_returns_404(client, db_session, user, other_user, auth_headers):
    feed = make_feed(db_session, user)
    other_cat = make_category(db_session, other_user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"category_ids": [other_cat.id]}, headers=auth_headers)
    assert resp.status_code == 404


def test_update_feed_auto_mark_read(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.auto_mark_read is False

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"auto_mark_read": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["auto_mark_read"] is True


def test_update_feed_default_open_action(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.default_open_action == "reader"

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"default_open_action": "original"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["default_open_action"] == "original"


def test_update_feed_default_open_action_rejects_invalid_value(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"default_open_action": "bogus"}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_importance_tier(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.importance_tier == "casual"

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"importance_tier": "must_read"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["importance_tier"] == "must_read"


def test_update_feed_importance_tier_rejects_invalid_value(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"importance_tier": "bogus"}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_manual_refresh_only(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.manual_refresh_only is False

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"manual_refresh_only": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["manual_refresh_only"] is True


def test_update_feed_note(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.note is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"note": "Great for weekend reading"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["note"] == "Great for weekend reading"


def test_update_feed_note_blank_clears_it(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, note="Old note")

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"note": "   "}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["note"] is None


def test_update_feed_note_rejects_too_long(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"note": "x" * 501}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_color(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.color is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"color": "#3B82F6"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["color"] == "#3B82F6"


def test_update_feed_color_rejects_invalid_format(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"color": "blue"}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_color_empty_string_clears_it(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, color="#3B82F6")

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"color": ""}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["color"] is None


def test_update_feed_icon_url_locks_it_against_auto_refresh(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, icon_url="https://example.com/auto-icon.png")
    assert feed.icon_locked is False

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"icon_url": "https://example.com/custom-icon.png"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["icon_url"] == "https://example.com/custom-icon.png"

    db_session.refresh(feed)
    assert feed.icon_locked is True


def test_list_feeds_orders_by_importance_tier(client, db_session, user, auth_headers):
    archive = make_feed(db_session, user, title="Archive Feed", importance_tier="archive_only")
    casual = make_feed(db_session, user, title="Casual Feed", importance_tier="casual")
    must_read = make_feed(db_session, user, title="Must Read Feed", importance_tier="must_read")

    resp = client.get("/api/v1/feeds", headers=auth_headers)
    assert resp.status_code == 200
    ids = [f["id"] for f in resp.json()["items"]]
    assert ids.index(must_read.id) < ids.index(casual.id) < ids.index(archive.id)


def test_update_feed_pinned(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.pinned is False

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"pinned": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True


def test_list_feeds_pinned_surfaces_above_importance_tier(client, db_session, user, auth_headers):
    must_read = make_feed(db_session, user, title="Must Read Feed", importance_tier="must_read")
    pinned_archive = make_feed(db_session, user, title="Pinned Archive Feed", importance_tier="archive_only", pinned=True)

    resp = client.get("/api/v1/feeds", headers=auth_headers)
    assert resp.status_code == 200
    ids = [f["id"] for f in resp.json()["items"]]
    assert ids.index(pinned_archive.id) < ids.index(must_read.id)


def test_snooze_and_unsnooze_feed(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, fetch_failure_count=5)

    resp = client.post(f"/api/v1/feeds/{feed.id}/snooze", json={"days": 7}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["health_snooze_until"] is not None
    assert body["fetch_failure_count"] == 0

    resp = client.delete(f"/api/v1/feeds/{feed.id}/snooze", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["health_snooze_until"] is None


def test_start_trial_and_keep_feed(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.trial_expires_at is None

    resp = client.post(f"/api/v1/feeds/{feed.id}/trial", json={"days": 14}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["trial_expires_at"] is not None

    resp = client.delete(f"/api/v1/feeds/{feed.id}/trial", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["trial_expires_at"] is None


def test_delete_feed_cascades_articles(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)
    article_id = article.id

    resp = client.delete(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 204

    from app.models import Article
    assert db_session.get(Article, article_id) is None


def test_refresh_feed_returns_new_article_count(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    with patch("app.routers.feeds.refresh_feed", new_callable=AsyncMock, return_value=3) as mock_refresh:
        resp = client.post(f"/api/v1/feeds/{feed.id}/refresh", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["feed_id"] == feed.id
    assert body["new_articles"] == 3
    # Manual refresh must bypass cached ETag/Last-Modified — some hosts echo
    # back stale validators and would otherwise 304 forever after the first fetch.
    mock_refresh.assert_awaited_once()
    assert mock_refresh.call_args.kwargs["force"] is True
    assert mock_refresh.call_args.args[0].id == feed.id


def test_refresh_feed_failure_returns_502(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    with patch("app.routers.feeds.refresh_feed", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        resp = client.post(f"/api/v1/feeds/{feed.id}/refresh", headers=auth_headers)
    assert resp.status_code == 502


# ── suggest_unsubscribe ──────────────────────────────────────────────────────

def test_suggest_unsubscribe_true_for_old_feed_never_read(client, db_session, user, auth_headers):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    feed = make_feed(db_session, user, created_at=old)
    make_article(db_session, feed, is_read=False)

    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suggest_unsubscribe"] is True


def test_suggest_unsubscribe_true_for_old_feed_stale_read(client, db_session, user, auth_headers):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    stale_read = datetime.now(timezone.utc) - timedelta(days=45)
    feed = make_feed(db_session, user, created_at=old)
    make_article(db_session, feed, is_read=True, read_at=stale_read)

    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suggest_unsubscribe"] is True


def test_suggest_unsubscribe_false_for_recently_read_feed(client, db_session, user, auth_headers):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    recent_read = datetime.now(timezone.utc) - timedelta(days=2)
    feed = make_feed(db_session, user, created_at=old)
    make_article(db_session, feed, is_read=True, read_at=recent_read)

    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suggest_unsubscribe"] is False


def test_suggest_unsubscribe_false_for_new_feed(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)  # created_at defaults to now
    make_article(db_session, feed, is_read=False)

    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suggest_unsubscribe"] is False


def test_suggest_unsubscribe_false_for_feed_with_no_articles(client, db_session, user, auth_headers):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    feed = make_feed(db_session, user, created_at=old)

    resp = client.get(f"/api/v1/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suggest_unsubscribe"] is False


def test_update_feed_auto_full_content(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.auto_full_content is True

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"auto_full_content": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["auto_full_content"] is False


def test_update_feed_suppress_duplicates(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.suppress_duplicates is False

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"suppress_duplicates": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["suppress_duplicates"] is True


def test_update_feed_refresh_interval_minutes(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.refresh_interval_minutes is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"refresh_interval_minutes": 120}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["refresh_interval_minutes"] == 120


def test_update_feed_refresh_interval_minutes_zero_clears_override(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, refresh_interval_minutes=120)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"refresh_interval_minutes": 0}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["refresh_interval_minutes"] is None


def test_update_feed_refresh_interval_minutes_rejects_out_of_range(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"refresh_interval_minutes": 5}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_retention_days(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.retention_days is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"retention_days": 90}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["retention_days"] == 90


def test_update_feed_retention_days_zero_clears_override(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, retention_days=90)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"retention_days": 0}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["retention_days"] is None


def test_update_feed_retention_days_rejects_out_of_range(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"retention_days": 5000}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_max_articles_retained(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.max_articles_retained is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"max_articles_retained": 500}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["max_articles_retained"] == 500


def test_update_feed_max_articles_retained_zero_clears_override(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, max_articles_retained=500)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"max_articles_retained": 0}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["max_articles_retained"] is None


def test_update_feed_max_articles_retained_rejects_out_of_range(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"max_articles_retained": 5}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_webhook_eligible(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.webhook_eligible is True

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"webhook_eligible": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["webhook_eligible"] is False


def test_update_feed_mute_and_boost_keywords(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.mute_keywords is None
    assert feed.boost_keywords is None

    resp = client.patch(
        f"/api/v1/feeds/{feed.id}",
        json={"mute_keywords": "crypto, politics", "boost_keywords": "breaking"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["mute_keywords"] == "crypto, politics"
    assert resp.json()["boost_keywords"] == "breaking"


def test_update_feed_keywords_empty_string_clears(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, mute_keywords="crypto")

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"mute_keywords": ""}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["mute_keywords"] is None


def test_update_feed_keywords_rejects_too_long(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"mute_keywords": "x" * 501}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_feed_min_content_length(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    assert feed.min_content_length is None

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"min_content_length": 200}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["min_content_length"] == 200


def test_update_feed_min_content_length_zero_clears_override(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user, min_content_length=200)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"min_content_length": 0}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["min_content_length"] is None


def test_update_feed_min_content_length_rejects_out_of_range(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)

    resp = client.patch(f"/api/v1/feeds/{feed.id}", json={"min_content_length": -5}, headers=auth_headers)
    assert resp.status_code == 422
