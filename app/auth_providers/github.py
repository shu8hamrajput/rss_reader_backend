"""GitHub OAuth2 provider. See ADR-005."""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .base import AuthProvider, UserInfo
from ..config import settings

_AUTH_URL     = "https://github.com/login/oauth/authorize"
_TOKEN_URL    = "https://github.com/login/oauth/access_token"
_USERINFO_URL = "https://api.github.com/user"
_EMAIL_URL    = "https://api.github.com/user/emails"


class GitHubAuthProvider(AuthProvider):
    name         = "github"
    display_name = "Sign in with GitHub"
    icon         = "🐙"

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        return f"{_AUTH_URL}?{urlencode({
            'client_id':    settings.github_client_id,
            'redirect_uri': redirect_uri,
            'scope':        'read:user user:email',
            'state':        state,
        })}"

    async def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                _TOKEN_URL,
                data={
                    "code":          code,
                    "client_id":     settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "redirect_uri":  redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"GitHub token exchange failed: {token_resp.text}")

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(status_code=502, detail=f"GitHub did not return an access token: {token_data}")

            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

            user_resp = await client.get(_USERINFO_URL, headers=headers)
            if user_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to fetch GitHub user info")
            info = user_resp.json()

            provider_id = str(info.get("id") or "")
            name        = info.get("name") or info.get("login")
            avatar_url  = info.get("avatar_url")

            # GitHub may not expose primary email in /user — fetch from /user/emails
            email = info.get("email")
            if not email:
                emails_resp = await client.get(_EMAIL_URL, headers=headers)
                if emails_resp.status_code == 200:
                    for entry in emails_resp.json():
                        if entry.get("primary") and entry.get("verified"):
                            email = entry.get("email")
                            break

            if not provider_id or not email:
                raise HTTPException(
                    status_code=502,
                    detail=f"GitHub user info missing required fields (id={provider_id!r}, email={email!r})",
                )

        return UserInfo(provider_id=provider_id, email=email, name=name, avatar_url=avatar_url)
