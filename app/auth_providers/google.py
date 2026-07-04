"""Google OAuth2 provider. See ADR-005."""
from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .base import AuthProvider, UserInfo
from ..config import settings

_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL    = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
_SCOPES       = "openid email profile"


class GoogleAuthProvider(AuthProvider):
    name         = "google"
    display_name = "Sign in with Google"
    icon         = "🔵"

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        return f"{_AUTH_URL}?{urlencode({
            'client_id':     settings.google_client_id,
            'redirect_uri':  redirect_uri,
            'response_type': 'code',
            'scope':         _SCOPES,
            'access_type':   'offline',
            'prompt':        'select_account',
            'state':         state,
        })}"

    async def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(_TOKEN_URL, data={
                "code":          code,
                "client_id":     settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            })
            if token_resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Google token exchange failed: {token_resp.text}")
            tokens = token_resp.json()

            user_resp = await client.get(
                _USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if user_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to fetch Google user info")
            info = user_resp.json()

        return UserInfo(
            provider_id = info["id"],
            email       = info["email"],
            name        = info.get("name"),
            avatar_url  = info.get("picture"),
        )
