from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import create_access_token
from app.routers.stream import _get_sse_user


def _request(headers=None):
    return MagicMock(headers=headers or {})


def test_get_sse_user_with_valid_api_key(db_session, user):
    user.api_token = "valid-api-key"
    db_session.commit()

    result = _get_sse_user(_request({"X-API-Key": "valid-api-key"}), None, None, db_session)

    assert result.id == user.id


def test_get_sse_user_with_valid_jwt(db_session, user):
    token, _ = create_access_token(user.id, user.email, user.token_version)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    result = _get_sse_user(_request(), None, credentials, db_session)

    assert result.id == user.id


def test_get_sse_user_with_token_query_param(db_session, user):
    token, _ = create_access_token(user.id, user.email, user.token_version)

    result = _get_sse_user(_request(), token, None, db_session)

    assert result.id == user.id


def test_get_sse_user_jwt_for_nonexistent_user_returns_401(db_session):
    token, _ = create_access_token(999999, "ghost@example.com", 0)

    with pytest.raises(HTTPException) as exc_info:
        _get_sse_user(_request(), token, None, db_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User not found"


def test_get_sse_user_no_credentials_returns_401(db_session):
    with pytest.raises(HTTPException) as exc_info:
        _get_sse_user(_request(), None, None, db_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Not authenticated"


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
