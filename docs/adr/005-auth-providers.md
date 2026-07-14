# ADR-005: Auth Provider Registry

**Status:** Implemented  
**Date:** 2026-07

## Context

`auth.py` hardcodes Google OAuth entirely — 456 lines of Google-specific code in the router.
Adding GitHub or Apple Sign-In would require duplicating the entire OAuth dance.

## Decision

Introduce an `AuthProvider` ABC in `app/auth_providers/`. The existing Google OAuth logic
moves to `GoogleAuthProvider`. The auth router becomes a generic dispatcher:
`GET /auth/{provider}` and `GET /auth/{provider}/callback` work for any registered provider.

```python
class AuthProvider(ABC):
    name: str               # slug used in URL: "google", "github"
    display_name: str
    scopes: list[str]

    @abstractmethod
    def authorization_url(self, redirect_uri: str, state: str) -> str: ...

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> UserInfo: ...

@dataclass
class UserInfo:
    provider_id: str    # provider's user ID
    email: str
    name: str | None
    avatar_url: str | None
```

## Generic auth routes

```
GET  /auth/{provider}/login     → provider.authorization_url(redirect_uri, state)
GET  /auth/{provider}/callback  → provider.exchange_code(code) → upsert user
                                   → issue JWT → one-time exchange code → redirect
POST /auth/exchange             → trade the one-time code above for the JWT
                                   (keeps the JWT out of the redirect URL/history)
GET  /auth/providers            → list registered providers for login page
```

`GitHubAuthProvider` (and any future provider added this way) is only registered — and
therefore only listed by `GET /auth/providers` — when its client ID/secret are configured
(`app/auth_providers/__init__.py`).

There is no generic `POST /auth/{provider}/token`. Google keeps its own dedicated
`POST /auth/google/token` for the mobile/SPA "client handles the redirect" flow; that
mechanism has not been generalized to other providers yet.

## Consequences

- Adding GitHub OAuth = one new `GitHubAuthProvider` file + one `register()` call.
- Login page (`GET /auth/providers`) is data-driven — no frontend hardcoding.
- `/auth/google` and `/auth/google/callback` are **not** aliases delegating to
  `GoogleAuthProvider` — they remain the original, separately-implemented Google-specific
  routes (`app/routers/auth.py`). The generic `/{provider}/...` routes are a parallel path
  used by GitHub today; consolidating Google onto the generic dispatcher is unfinished work.
- Desktop flow (`/auth/google/desktop-url`) stays on the Google-specific implementation, not `GoogleAuthProvider`.
