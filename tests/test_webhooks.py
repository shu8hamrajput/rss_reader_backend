def test_create_webhook(client, auth_headers):
    resp = client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["new_article", "highlight_created"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://example.com/hook"
    assert sorted(body["events"]) == ["highlight_created", "new_article"]
    assert body["is_active"] is True


def test_create_webhook_invalid_event_type(client, auth_headers):
    resp = client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["not_a_real_event"]},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_webhook_duplicate_url_returns_existing(client, auth_headers):
    first = client.post("/api/v1/webhooks", json={"url": "https://example.com/dup"}, headers=auth_headers).json()
    second = client.post("/api/v1/webhooks", json={"url": "https://example.com/dup"}, headers=auth_headers).json()
    assert second["id"] == first["id"]


def test_list_webhooks_ordered_by_created_at(client, auth_headers):
    client.post("/api/v1/webhooks", json={"url": "https://example.com/a"}, headers=auth_headers)
    client.post("/api/v1/webhooks", json={"url": "https://example.com/b"}, headers=auth_headers)

    resp = client.get("/api/v1/webhooks", headers=auth_headers)
    assert resp.status_code == 200
    urls = [w["url"] for w in resp.json()]
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_toggle_webhook(client, auth_headers):
    created = client.post("/api/v1/webhooks", json={"url": "https://example.com/toggle"}, headers=auth_headers).json()
    assert created["is_active"] is True

    resp = client.patch(f"/api/v1/webhooks/{created['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp = client.patch(f"/api/v1/webhooks/{created['id']}", headers=auth_headers)
    assert resp.json()["is_active"] is True


def test_delete_webhook(client, auth_headers):
    created = client.post("/api/v1/webhooks", json={"url": "https://example.com/delete"}, headers=auth_headers).json()

    resp = client.delete(f"/api/v1/webhooks/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/webhooks", headers=auth_headers)
    assert resp.json() == []


def test_webhook_not_owned_returns_404(client, auth_headers, other_auth_headers):
    created = client.post("/api/v1/webhooks", json={"url": "https://example.com/other"}, headers=other_auth_headers).json()

    resp = client.patch(f"/api/v1/webhooks/{created['id']}", headers=auth_headers)
    assert resp.status_code == 404

    resp = client.delete(f"/api/v1/webhooks/{created['id']}", headers=auth_headers)
    assert resp.status_code == 404
