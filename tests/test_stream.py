def test_stream_articles_no_auth_returns_401(client):
    resp = client.get("/api/v1/stream/articles")
    assert resp.status_code == 401


def test_stream_articles_garbage_bearer_returns_401(client):
    resp = client.get("/api/v1/stream/articles", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_stream_articles_revoked_token_returns_401(client, db_session, user, auth_headers):
    user.token_version += 1
    db_session.commit()

    resp = client.get("/api/v1/stream/articles", headers=auth_headers)
    assert resp.status_code == 401


def test_stream_articles_invalid_api_key_returns_401(client):
    resp = client.get("/api/v1/stream/articles", headers={"X-API-Key": "bogus-key"})
    assert resp.status_code == 401
