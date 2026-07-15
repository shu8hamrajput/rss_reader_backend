from app.models import CollectionItem

from .conftest import make_collection, make_feed


def test_create_collection_dedupes_items_by_normalized_url(client, auth_headers):
    resp = client.post(
        "/api/v1/collections",
        json={
            "name": "My Feeds",
            "items": [
                {"feed_url": "https://example.com/a.xml"},
                {"feed_url": "https://EXAMPLE.com/a.xml/"},
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "my-feeds"
    assert len(body["items"]) == 1


def test_create_collection_slug_collision_appends_suffix(client, auth_headers):
    first = client.post("/api/v1/collections", json={"name": "My Feeds"}, headers=auth_headers).json()
    second = client.post("/api/v1/collections", json={"name": "My Feeds"}, headers=auth_headers).json()
    assert first["slug"] == "my-feeds"
    assert second["slug"] == "my-feeds-2"


def test_list_my_collections_only_mine(client, db_session, user, other_user, auth_headers):
    make_collection(db_session, user, name="Mine")
    make_collection(db_session, other_user, name="Theirs")

    resp = client.get("/api/v1/collections/mine", headers=auth_headers)
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert names == ["Mine"]


def test_get_collection_private_not_owned_returns_404(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user, is_public=False)

    resp = client.get(f"/api/v1/collections/{collection.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_get_collection_public_not_owned(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user, is_public=True)

    resp = client.get(f"/api/v1/collections/{collection.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_owner"] is False
    assert body["owner_name"] == other_user.name


def test_update_collection_renames_and_regenerates_slug(client, auth_headers):
    created = client.post("/api/v1/collections", json={"name": "Original Name"}, headers=auth_headers).json()

    resp = client.patch(f"/api/v1/collections/{created['id']}", json={"name": "Renamed"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["slug"] == "renamed"


def test_update_collection_not_owned_returns_404(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user)

    resp = client.patch(f"/api/v1/collections/{collection.id}", json={"name": "Hijacked"}, headers=auth_headers)
    assert resp.status_code == 404


def test_delete_collection(client, auth_headers):
    created = client.post("/api/v1/collections", json={"name": "To delete"}, headers=auth_headers).json()

    resp = client.delete(f"/api/v1/collections/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/collections/mine", headers=auth_headers)
    assert resp.json() == []


def test_delete_collection_not_owned_returns_404(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user)

    resp = client.delete(f"/api/v1/collections/{collection.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_add_collection_item_duplicate_returns_409(client, auth_headers):
    created = client.post(
        "/api/v1/collections",
        json={"name": "Items", "items": [{"feed_url": "https://example.com/a.xml"}]},
        headers=auth_headers,
    ).json()

    resp = client.post(
        f"/api/v1/collections/{created['id']}/items",
        json={"feed_url": "https://EXAMPLE.com/a.xml/"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_remove_collection_item_unknown_returns_404(client, auth_headers):
    created = client.post("/api/v1/collections", json={"name": "Items"}, headers=auth_headers).json()

    resp = client.delete(f"/api/v1/collections/{created['id']}/items/999999", headers=auth_headers)
    assert resp.status_code == 404


def test_subscribe_collection_adds_feeds_and_is_idempotent(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user, is_public=True)
    db_session.add_all([
        CollectionItem(collection_id=collection.id, feed_url="https://example.com/sub-a.xml", position=0),
        CollectionItem(collection_id=collection.id, feed_url="https://example.com/sub-b.xml", position=1),
    ])
    db_session.commit()

    resp = client.post(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"subscribed": True, "feeds_added": 2}

    db_session.refresh(collection)
    assert collection.subscriber_count == 1

    from app.models import Feed
    added_feed = db_session.query(Feed).filter(Feed.url == "https://example.com/sub-a.xml").first()
    assert added_feed.discovered_via == "collection"

    resp = client.post(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"subscribed": True, "feeds_added": 0}

    db_session.refresh(collection)
    assert collection.subscriber_count == 1


def test_subscribe_collection_respects_max_feeds_limit(client, db_session, user, other_user, auth_headers):
    for i in range(25):
        make_feed(db_session, user, url=f"https://example.com/existing-{i}.xml")

    collection = make_collection(db_session, other_user, is_public=True)
    db_session.add(CollectionItem(collection_id=collection.id, feed_url="https://example.com/over-limit.xml", position=0))
    db_session.commit()

    resp = client.post(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["feeds_added"] == 0


def test_subscribe_private_collection_not_owned_returns_404(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user, is_public=False)

    resp = client.post(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 404


def test_unsubscribe_collection(client, db_session, other_user, auth_headers):
    collection = make_collection(db_session, other_user, is_public=True)

    resp = client.delete(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 404

    client.post(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)

    resp = client.delete(f"/api/v1/collections/{collection.id}/subscribe", headers=auth_headers)
    assert resp.status_code == 204

    db_session.refresh(collection)
    assert collection.subscriber_count == 0


def test_discover_collections_only_public_with_search_and_pagination(client, db_session, user, other_user, auth_headers):
    make_collection(db_session, user, name="Private News", is_public=False)
    make_collection(db_session, other_user, name="Tech News", is_public=True, description="All things tech")
    make_collection(db_session, other_user, name="Cooking Tips", is_public=True)

    resp = client.get("/api/v1/collections/discover", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {c["name"] for c in body["items"]}
    assert names == {"Tech News", "Cooking Tips"}

    resp = client.get("/api/v1/collections/discover?search=tech", headers=auth_headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Tech News"

    resp = client.get("/api/v1/collections/discover?page=1&page_size=1", headers=auth_headers)
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
