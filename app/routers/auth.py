"""
Google OAuth2 authentication.

Two flows are supported:
  1. Web / server-side redirect
       GET  /auth/google            → redirects browser to Google consent screen
       GET  /auth/google/callback   → Google redirects here; issues JWT
  2. Mobile / SPA  (client handles the browser redirect itself)
       POST /auth/google/token      → exchange {code, redirect_uri} for JWT

After either flow the client receives a TokenResponse with a Bearer JWT.
Every subsequent request must include:  Authorization: Bearer <token>
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import create_access_token, generate_oauth_state, get_current_user, verify_oauth_state
from ..config import settings
from ..database import get_db
from ..models import User, UserPreferences, UserSession
import secrets
from ..schemas import ApiTokenResponse, GoogleTokenRequest, PreferencesResponse, PreferencesUpdate, SessionResponse, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

_SCOPES = "openid email profile"


def _google_auth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def _exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for Google tokens + user info."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Google token exchange failed: {token_resp.text}")
        tokens = token_resp.json()

        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch Google user info")
        return user_resp.json()


def _record_session(user: User, request: "Request | None", db: Session) -> None:
    """Create or refresh a session record for the user based on IP + device fingerprint."""
    from ..models import UserSession
    if request is None:
        return
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent", "")[:512]
    # Update existing session from same IP+UA pair, or create a new one
    existing = db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.ip_address == ip,
        UserSession.device_info == ua,
    ).first()
    now = datetime.now(timezone.utc)
    if existing:
        existing.last_seen_at = now
    else:
        db.add(UserSession(user_id=user.id, ip_address=ip, device_info=ua, last_seen_at=now))


def _upsert_user(google_info: dict, db: Session) -> User:
    google_id = google_info["id"]
    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        user.name = google_info.get("name", user.name)
        user.avatar_url = google_info.get("picture", user.avatar_url)
        user.last_login_at = datetime.now(timezone.utc)
    else:
        user = User(
            google_id=google_id,
            email=google_info["email"],
            name=google_info.get("name"),
            avatar_url=google_info.get("picture"),
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _build_token_response(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user.id, user.email, user.token_version)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


def _require_google_config() -> None:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env",
        )


# ── Web redirect flow ─────────────────────────────────────────────────────────

@router.get(
    "/google",
    summary="Redirect browser to Google consent screen",
    description=(
        "Redirects the user's browser to Google's OAuth2 consent screen. "
        "After login Google will redirect to `/auth/google/callback`. "
        "A signed state cookie is set to prevent CSRF."
    ),
    response_class=RedirectResponse,
    status_code=302,
)
async def google_login(response: Response):
    _require_google_config()
    state = generate_oauth_state()
    url = _google_auth_url(settings.google_redirect_uri, state)
    redirect = RedirectResponse(url=url, status_code=302)
    redirect.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return redirect


@router.get(
    "/google/callback",
    response_class=RedirectResponse,
    summary="Google OAuth2 callback (web flow)",
    description=(
        "Google redirects here after user consent. "
        "Verifies state, exchanges the code, creates/updates the user, "
        "and redirects the browser back to the frontend with `?token=<jwt>`."
    ),
    status_code=302,
)
async def google_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    oauth_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    _require_google_config()

    if not oauth_state or not verify_oauth_state(oauth_state) or oauth_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — possible CSRF attempt")

    google_info = await _exchange_code(code, settings.google_redirect_uri)
    user = _upsert_user(google_info, db)
    _record_session(user, request, db)
    db.commit()
    token_response = _build_token_response(user)

    separator = '&' if '?' in settings.frontend_url else '?'
    redirect_url = f"{settings.frontend_url}{separator}{urlencode({'token': token_response.access_token})}"

    redirect = RedirectResponse(url=redirect_url, status_code=302)
    redirect.delete_cookie('oauth_state')
    return redirect


# ── Desktop native app helpers ───────────────────────────────────────────────

# Redirect URIs allowed for native desktop clients (loopback per RFC 8252).
_DESKTOP_ALLOWED_REDIRECTS: frozenset[str] = frozenset({
    "http://127.0.0.1:8899/callback",
    "http://localhost:8899/callback",
})


@router.get(
    "/google/desktop-url",
    summary="Return Google OAuth URL for desktop/native app flow",
    description=(
        "Desktop clients that cannot use browser redirects call this to obtain "
        "the Google consent URL with a loopback redirect_uri (RFC 8252). "
        "They open the URL in the system browser, capture the code on :8899, "
        "then exchange it via POST /google/token."
    ),
)
def google_desktop_url(redirect_uri: str = Query(..., description="Loopback callback URI")):
    _require_google_config()
    if redirect_uri not in _DESKTOP_ALLOWED_REDIRECTS:
        raise HTTPException(
            status_code=400,
            detail=f"redirect_uri not in desktop allowlist. Allowed: {sorted(_DESKTOP_ALLOWED_REDIRECTS)}",
        )
    state = generate_oauth_state()
    url = _google_auth_url(redirect_uri, state)
    return {"url": url, "state": state}


# ── Mobile / SPA token exchange ───────────────────────────────────────────────

@router.post(
    "/google/token",
    response_model=TokenResponse,
    summary="Exchange Google auth code for a JWT (mobile / SPA flow)",
    description=(
        "For clients that handle the OAuth redirect themselves (mobile apps, SPAs). "
        "Send the `code` and the exact `redirect_uri` used to obtain it. "
        "Returns a JWT on success."
    ),
)
async def google_token_exchange(request: Request, payload: GoogleTokenRequest, db: Session = Depends(get_db)):
    _require_google_config()
    google_info = await _exchange_code(payload.code, payload.redirect_uri)
    user = _upsert_user(google_info, db)
    _record_session(user, request, db)
    db.commit()
    return _build_token_response(user)


# ── Token refresh ─────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Issue a fresh JWT without re-authenticating with Google",
    description=(
        "Call with a still-valid Bearer JWT to receive a new token with a reset expiry. "
        "Use this to silently extend sessions on page load or before making long background requests."
    ),
)
def refresh_token(current_user: User = Depends(get_current_user)):
    return _build_token_response(current_user)


# ── Current user ──────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the currently authenticated user",
)
def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me/preferences",
    response_model=UserResponse,
    summary="Sync the current user's client-side preferences",
)
def update_my_preferences(
    payload: PreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Shallow-merges the given top-level sections into the stored preferences blob."""
    merged = dict(current_user.preferences or {})
    merged.update(payload.preferences)
    current_user.preferences = merged
    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


def _get_or_create_user_prefs(user_id: int, db: Session) -> UserPreferences:
    row = db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()
    if not row:
        row = UserPreferences(user_id=user_id, preferences={})
        db.add(row)
        db.flush()
    return row


@router.get(
    "/me/preferences",
    response_model=PreferencesResponse,
    summary="Get the current user's synced preferences",
)
def get_my_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = _get_or_create_user_prefs(current_user.id, db)
    db.commit()
    return PreferencesResponse(preferences=row.preferences, updated_at=row.updated_at)


@router.put(
    "/me/preferences",
    response_model=PreferencesResponse,
    summary="Replace the current user's synced preferences",
)
def put_my_preferences(
    payload: PreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full replacement of top-level sections; shallow-merges sections within the blob
    so a client pushing only {settings: ...} doesn't wipe layout or saved_searches."""
    row = _get_or_create_user_prefs(current_user.id, db)
    merged = dict(row.preferences or {})
    merged.update(payload.preferences)
    row.preferences = merged
    db.commit()
    db.refresh(row)
    return PreferencesResponse(preferences=row.preferences, updated_at=row.updated_at)


@router.post(
    "/signout-everywhere",
    response_model=UserResponse,
    summary="Invalidate all existing sessions",
    description=(
        "Bumps the user's token_version, instantly revoking all previously issued JWTs. "
        "The caller's own token becomes invalid after this call — they should clear their "
        "local token and redirect to login."
    ),
)
def signout_everywhere(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Invalidate all existing sessions by bumping token_version."""
    current_user.token_version += 1
    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


# ── Personal API token ────────────────────────────────────────────────────────

@router.post(
    "/me/token",
    response_model=ApiTokenResponse,
    summary="Generate a personal API token",
    description=(
        "Generates a new personal API token. Any previous token is revoked. "
        "Pass the token as `X-API-Key: <token>` on API requests as an alternative to Bearer JWT."
    ),
)
def generate_api_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token = secrets.token_hex(32)
    current_user.api_token = token
    db.commit()
    return ApiTokenResponse(api_token=token)


@router.delete(
    "/me/token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the personal API token",
)
def revoke_api_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.api_token = None
    db.commit()


# ── Delete account ────────────────────────────────────────────────────────────

@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete the authenticated user's account and all data",
)
def delete_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.delete(current_user)
    db.commit()


# ── Active Sessions ───────────────────────────────────────────────────────────

@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List active login sessions",
)
def list_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(UserSession)
        .filter(UserSession.user_id == current_user.id)
        .order_by(UserSession.last_seen_at.desc())
        .limit(50)
        .all()
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    summary="Remove a login session record",
)
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == current_user.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
