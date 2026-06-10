from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.auth import create_access_token
from app.config import settings
from app.models import User, UserSession

from .conftest import auth_headers_for


def test_get_me_requires_auth(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_get_me_rejects_garbage_token(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_get_me_rejects_revoked_token_version(client, user):
    token, _ = create_access_token(user.id, user.email, token_version=user.token_version + 1)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_get_me_returns_user(client, user, auth_headers):
    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.id
    assert body["email"] == user.email


def test_patch_preferences_shallow_merge(client, auth_headers):
    resp = client.patch(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"settings": {"theme": "dark"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {"settings": {"theme": "dark"}}

    resp = client.patch(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"layout": {"sidebar": "collapsed"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs["settings"] == {"theme": "dark"}
    assert prefs["layout"] == {"sidebar": "collapsed"}

    # Shallow merge: re-setting "settings" replaces the whole section
    resp = client.patch(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"settings": {"font": "serif"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs["settings"] == {"font": "serif"}
    assert prefs["layout"] == {"sidebar": "collapsed"}


def test_get_preferences_defaults_empty(client, auth_headers):
    resp = client.get("/api/v1/auth/me/preferences", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {}


def test_put_preferences_preserves_other_sections(client, auth_headers):
    resp = client.put(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"settings": {"theme": "dark"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {"settings": {"theme": "dark"}}

    resp = client.put(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"layout": {"sidebar": "collapsed"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs["settings"] == {"theme": "dark"}
    assert prefs["layout"] == {"sidebar": "collapsed"}


def test_signout_everywhere_revokes_existing_token(client, user, auth_headers):
    resp = client.post("/api/v1/auth/signout-everywhere", headers=auth_headers)
    assert resp.status_code == 200

    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 401


def test_api_token_lifecycle(client, auth_headers):
    resp = client.post("/api/v1/auth/me/token", headers=auth_headers)
    assert resp.status_code == 200
    api_token = resp.json()["api_token"]

    resp = client.get("/api/v1/auth/me", headers={"X-API-Key": api_token})
    assert resp.status_code == 200

    resp = client.delete("/api/v1/auth/me/token", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/auth/me", headers={"X-API-Key": api_token})
    assert resp.status_code == 401


def test_list_and_delete_sessions(client, db_session, user, other_user, auth_headers):
    now = datetime.now(timezone.utc)
    older = UserSession(user_id=user.id, device_info="old-device", ip_address="1.1.1.1", last_seen_at=now - timedelta(days=1))
    newer = UserSession(user_id=user.id, device_info="new-device", ip_address="2.2.2.2", last_seen_at=now)
    other_session = UserSession(user_id=other_user.id, device_info="other-device", ip_address="3.3.3.3", last_seen_at=now)
    db_session.add_all([older, newer, other_session])
    db_session.commit()

    resp = client.get("/api/v1/auth/sessions", headers=auth_headers)
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [newer.id, older.id]

    resp = client.delete(f"/api/v1/auth/sessions/{other_session.id}", headers=auth_headers)
    assert resp.status_code == 404

    resp = client.delete(f"/api/v1/auth/sessions/{newer.id}", headers=auth_headers)
    assert resp.status_code == 204


def test_google_token_exchange_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")

    resp = client.post(
        "/api/v1/auth/google/token",
        json={"code": "abc", "redirect_uri": "https://app.example.com/callback"},
    )
    assert resp.status_code == 503


def test_google_token_exchange_creates_user(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")

    fake_profile = {
        "id": "google-new-user",
        "email": "newuser@example.com",
        "name": "New User",
        "picture": "https://example.com/avatar.png",
    }

    with patch("app.routers.auth._exchange_code", new_callable=AsyncMock, return_value=fake_profile):
        resp = client.post(
            "/api/v1/auth/google/token",
            json={"code": "abc123", "redirect_uri": "https://app.example.com/callback"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "newuser@example.com"
    assert body["access_token"]

    created = db_session.query(User).filter(User.google_id == "google-new-user").first()
    assert created is not None
    assert created.email == "newuser@example.com"


def test_delete_account(client, user, auth_headers):
    resp = client.delete("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 401
