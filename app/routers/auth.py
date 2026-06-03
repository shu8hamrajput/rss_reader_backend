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
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import create_access_token, generate_oauth_state, get_current_user, verify_oauth_state
from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import GoogleTokenRequest, TokenResponse, UserResponse

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
    token, expires_in = create_access_token(user.id, user.email)
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
    response_model=TokenResponse,
    summary="Google OAuth2 callback (web flow)",
    description=(
        "Google redirects here after user consent. "
        "Verifies state, exchanges the code, creates/updates the user, "
        "and returns a JWT. For a SPA you can redirect to the frontend with "
        "`?token=<jwt>` instead."
    ),
)
async def google_callback(
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
    return _build_token_response(user)


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
async def google_token_exchange(payload: GoogleTokenRequest, db: Session = Depends(get_db)):
    _require_google_config()
    google_info = await _exchange_code(payload.code, payload.redirect_uri)
    user = _upsert_user(google_info, db)
    return _build_token_response(user)


# ── Current user ──────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the currently authenticated user",
)
def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
