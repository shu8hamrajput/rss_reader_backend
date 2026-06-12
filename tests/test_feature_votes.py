def test_create_feature_vote(client, auth_headers):
    resp = client.post("/api/v1/feature-votes", json={"feature_key": "search_alerts"}, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["feature_key"] == "search_alerts"


def test_create_feature_vote_duplicate_returns_existing(client, auth_headers):
    first = client.post("/api/v1/feature-votes", json={"feature_key": "search_alerts"}, headers=auth_headers).json()
    second = client.post("/api/v1/feature-votes", json={"feature_key": "search_alerts"}, headers=auth_headers).json()
    assert second["id"] == first["id"]


def test_list_feature_votes_scoped_to_current_user(client, auth_headers, other_auth_headers):
    client.post("/api/v1/feature-votes", json={"feature_key": "search_alerts"}, headers=auth_headers)
    client.post("/api/v1/feature-votes", json={"feature_key": "email_digest"}, headers=other_auth_headers)

    resp = client.get("/api/v1/feature-votes", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == ["search_alerts"]


def test_create_feature_vote_invalid_key_returns_422(client, auth_headers):
    resp = client.post("/api/v1/feature-votes", json={"feature_key": "not_a_real_feature"}, headers=auth_headers)
    assert resp.status_code == 422
