def test_get_preferences_defaults_empty(client, auth_headers):
    resp = client.get("/api/v1/me/preferences", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {}


def test_put_preferences_stores_blob(client, auth_headers):
    resp = client.put("/api/v1/me/preferences", json={"theme": "dark"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {"theme": "dark"}


def test_put_preferences_replaces_entirely(client, auth_headers):
    client.put("/api/v1/me/preferences", json={"theme": "dark"}, headers=auth_headers)

    resp = client.put("/api/v1/me/preferences", json={"layout": "compact"}, headers=auth_headers)
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs == {"layout": "compact"}
    assert "theme" not in prefs


def test_put_preferences_rejects_non_object_body(client, auth_headers):
    resp = client.put("/api/v1/me/preferences", json=["not", "an", "object"], headers=auth_headers)
    assert resp.status_code == 422
