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
