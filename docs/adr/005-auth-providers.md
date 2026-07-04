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
GET  /auth/{provider}           → provider.authorization_url(redirect_uri, state)
GET  /auth/{provider}/callback  → provider.exchange_code(code) → upsert user → JWT
POST /auth/{provider}/token     → SPA/desktop code exchange
GET  /auth/providers            → list registered providers for login page
```

## Consequences

- Adding GitHub OAuth = one new `GitHubAuthProvider` file + one `register()` call.
- Login page (`GET /auth/providers`) is data-driven — no frontend hardcoding.
- Existing `/auth/google` routes remain as aliases for backward compatibility.
- Desktop flow (`/auth/google/desktop-url`) moves into `GoogleAuthProvider`.
