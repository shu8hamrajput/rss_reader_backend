from .conftest import make_category, make_feed


def test_create_category(client, auth_headers):
    resp = client.post("/api/v1/categories", json={"name": "Tech"}, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Tech"
    assert body["feed_count"] == 0


def test_create_category_duplicate_name(client, auth_headers):
    client.post("/api/v1/categories", json={"name": "Tech"}, headers=auth_headers)
    resp = client.post("/api/v1/categories", json={"name": "Tech"}, headers=auth_headers)
    assert resp.status_code == 409


def test_create_category_empty_name(client, auth_headers):
    resp = client.post("/api/v1/categories", json={"name": "   "}, headers=auth_headers)
    assert resp.status_code == 422


def test_list_categories_ordered_by_name(client, db_session, user, auth_headers):
    make_category(db_session, user, name="Zebra")
    make_category(db_session, user, name="Alpha")

    resp = client.get("/api/v1/categories", headers=auth_headers)
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert names == sorted(names)
    assert "Alpha" in names and "Zebra" in names


def test_list_categories_includes_feed_count(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    feed = make_feed(db_session, user)
    feed.categories.append(cat)
    db_session.commit()

    resp = client.get("/api/v1/categories", headers=auth_headers)
    assert resp.status_code == 200
    found = next(c for c in resp.json() if c["id"] == cat.id)
    assert found["feed_count"] == 1


def test_get_category(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    resp = client.get(f"/api/v1/categories/{cat.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == cat.id


def test_get_category_not_owned_returns_404(client, db_session, other_user, auth_headers):
    cat = make_category(db_session, other_user)
    resp = client.get(f"/api/v1/categories/{cat.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_update_category_rename(client, db_session, user, auth_headers):
    cat = make_category(db_session, user, name="Old Name")
    resp = client.patch(f"/api/v1/categories/{cat.id}", json={"name": "New Name"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_category_rename_to_existing_name_conflicts(client, db_session, user, auth_headers):
    make_category(db_session, user, name="Existing")
    cat = make_category(db_session, user, name="Other")

    resp = client.patch(f"/api/v1/categories/{cat.id}", json={"name": "Existing"}, headers=auth_headers)
    assert resp.status_code == 409


def test_delete_category_keeps_feeds(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    feed = make_feed(db_session, user)
    feed.categories.append(cat)
    db_session.commit()
    feed_id = feed.id

    resp = client.delete(f"/api/v1/categories/{cat.id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/feeds/{feed_id}", headers=auth_headers)
    assert resp.status_code == 200


def test_add_feed_to_category(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    feed = make_feed(db_session, user)

    resp = client.post(f"/api/v1/categories/{cat.id}/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["feed_count"] == 1

    # Idempotent: adding again doesn't duplicate
    resp = client.post(f"/api/v1/categories/{cat.id}/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["feed_count"] == 1


def test_add_feed_to_category_not_owned_feed_404(client, db_session, user, other_user, auth_headers):
    cat = make_category(db_session, user)
    feed = make_feed(db_session, other_user)

    resp = client.post(f"/api/v1/categories/{cat.id}/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_remove_feed_from_category(client, db_session, user, auth_headers):
    cat = make_category(db_session, user)
    feed = make_feed(db_session, user)
    feed.categories.append(cat)
    db_session.commit()

    resp = client.delete(f"/api/v1/categories/{cat.id}/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/categories", headers=auth_headers)
    found = next(c for c in resp.json() if c["id"] == cat.id)
    assert found["feed_count"] == 0


def test_remove_feed_from_category_not_owned_category_404(client, db_session, user, other_user, auth_headers):
    cat = make_category(db_session, other_user)
    feed = make_feed(db_session, user)

    resp = client.delete(f"/api/v1/categories/{cat.id}/feeds/{feed.id}", headers=auth_headers)
    assert resp.status_code == 404
