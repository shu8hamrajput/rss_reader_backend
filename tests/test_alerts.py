import json
from datetime import datetime, timedelta, timezone

from app.models import AlertMatch, SearchAlert

from .conftest import make_feed


def test_create_alert(client, auth_headers):
    resp = client.post("/api/v1/alerts", json={"query": "rust async", "label": "Rust"}, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["query"] == "rust async"
    assert body["label"] == "Rust"


def test_create_alert_duplicate_query_returns_existing(client, auth_headers):
    first = client.post("/api/v1/alerts", json={"query": "rust async"}, headers=auth_headers).json()
    second = client.post("/api/v1/alerts", json={"query": "rust async"}, headers=auth_headers).json()
    assert second["id"] == first["id"]


def test_list_alerts_ordered_by_created_at(client, auth_headers):
    client.post("/api/v1/alerts", json={"query": "first"}, headers=auth_headers)
    client.post("/api/v1/alerts", json={"query": "second"}, headers=auth_headers)

    resp = client.get("/api/v1/alerts", headers=auth_headers)
    assert resp.status_code == 200
    queries = [a["query"] for a in resp.json()]
    assert queries == ["first", "second"]


def test_delete_alert(client, auth_headers):
    created = client.post("/api/v1/alerts", json={"query": "to delete"}, headers=auth_headers).json()

    resp = client.delete(f"/api/v1/alerts/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/alerts", headers=auth_headers)
    assert resp.json() == []


def test_delete_alert_not_owned_returns_404(client, db_session, other_user, auth_headers, other_auth_headers):
    created = client.post("/api/v1/alerts", json={"query": "other's alert"}, headers=other_auth_headers).json()

    resp = client.delete(f"/api/v1/alerts/{created['id']}", headers=auth_headers)
    assert resp.status_code == 404


def test_list_alert_matches_empty(client, auth_headers):
    created = client.post("/api/v1/alerts", json={"query": "rust async"}, headers=auth_headers).json()

    resp = client.get(f"/api/v1/alerts/{created['id']}/matches", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_alert_matches_returns_newest_first(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    alert = SearchAlert(user_id=user.id, query="rust async")
    db_session.add(alert)
    db_session.commit()

    older = AlertMatch(
        alert_id=alert.id, feed_id=feed.id, article_ids=json.dumps([1]), count=1,
        matched_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    newer = AlertMatch(
        alert_id=alert.id, feed_id=feed.id, article_ids=json.dumps([2, 3]), count=2,
        matched_at=datetime.now(timezone.utc),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    resp = client.get(f"/api/v1/alerts/{alert.id}/matches", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert [m["count"] for m in body] == [2, 1]
    assert body[0]["article_ids"] == [2, 3]


def test_list_alert_matches_not_owned_returns_404(client, db_session, other_user, auth_headers):
    alert = SearchAlert(user_id=other_user.id, query="other's alert")
    db_session.add(alert)
    db_session.commit()

    resp = client.get(f"/api/v1/alerts/{alert.id}/matches", headers=auth_headers)
    assert resp.status_code == 404


def test_list_alert_matches_unknown_alert_returns_404(client, auth_headers):
    resp = client.get("/api/v1/alerts/999999/matches", headers=auth_headers)
    assert resp.status_code == 404
