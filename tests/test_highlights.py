from app.models import Highlight

from .conftest import make_article, make_feed


def test_create_highlight(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 10, "end_pos": 20, "color_id": 2, "text": "highlighted text"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["start_pos"] == 10
    assert body["end_pos"] == 20
    assert body["color_id"] == 2
    assert body["text"] == "highlighted text"


def test_create_highlight_invalid_range(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 20, "end_pos": 10},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_highlight_invalid_color(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    resp = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 0, "end_pos": 10, "color_id": 9},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_list_highlights_ordered_by_start_pos(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 50, "end_pos": 60},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 5, "end_pos": 15},
        headers=auth_headers,
    )

    resp = client.get(f"/api/v1/articles/{article.id}/highlights", headers=auth_headers)
    assert resp.status_code == 200
    starts = [h["start_pos"] for h in resp.json()]
    assert starts == sorted(starts)


def test_update_highlight(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)
    created = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 0, "end_pos": 10, "color_id": 1},
        headers=auth_headers,
    ).json()

    resp = client.patch(
        f"/api/v1/highlights/{created['id']}",
        json={"color_id": 3, "note": "important"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["color_id"] == 3
    assert body["note"] == "important"


def test_update_highlight_not_owned_returns_404(client, db_session, user, other_user, auth_headers):
    feed = make_feed(db_session, other_user)
    article = make_article(db_session, feed)
    highlight = Highlight(user_id=other_user.id, article_id=article.id, start_pos=0, end_pos=5, color_id=1)
    db_session.add(highlight)
    db_session.commit()

    resp = client.patch(f"/api/v1/highlights/{highlight.id}", json={"color_id": 2}, headers=auth_headers)
    assert resp.status_code == 404


def test_delete_highlight(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)
    created = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 0, "end_pos": 10},
        headers=auth_headers,
    ).json()

    resp = client.delete(f"/api/v1/highlights/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/articles/{article.id}/highlights", headers=auth_headers)
    assert resp.json() == []


def test_review_queue_never_reviewed_first(client, db_session, user, auth_headers):
    from datetime import datetime, timezone

    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)

    reviewed = Highlight(
        user_id=user.id, article_id=article.id, start_pos=0, end_pos=5, color_id=1,
        reviewed_at=datetime.now(timezone.utc),
    )
    never_reviewed = Highlight(user_id=user.id, article_id=article.id, start_pos=10, end_pos=15, color_id=1)
    db_session.add_all([reviewed, never_reviewed])
    db_session.commit()

    resp = client.get("/api/v1/highlights/review", headers=auth_headers)
    assert resp.status_code == 200
    ids = [h["id"] for h in resp.json()]
    assert ids.index(never_reviewed.id) < ids.index(reviewed.id)


def test_mark_highlight_reviewed(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed)
    created = client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 0, "end_pos": 10},
        headers=auth_headers,
    ).json()

    resp = client.post(f"/api/v1/highlights/{created['id']}/reviewed", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["reviewed_at"] is not None


def test_export_highlights(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, title="My Article", url="https://example.com/a")
    client.post(
        f"/api/v1/articles/{article.id}/highlights",
        json={"start_pos": 0, "end_pos": 10, "text": "snippet"},
        headers=auth_headers,
    )

    resp = client.get("/api/v1/highlights/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == "attachment; filename=highlights.json"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["article_title"] == "My Article"
    assert body[0]["article_url"] == "https://example.com/a"
    assert body[0]["text"] == "snippet"
