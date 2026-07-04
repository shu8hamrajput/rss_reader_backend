"""Auth provider protocol. See ADR-005."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class UserInfo:
    """Normalised user identity returned by any OAuth provider."""
    provider_id: str      # provider's opaque user ID
    email: str
    name: str | None = None
    avatar_url: str | None = None


class AuthProvider(ABC):
    name: str             # slug used in URL path: "google", "github"
    display_name: str     # "Sign in with Google"
    icon: str = ""        # emoji or URL

    @abstractmethod
    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """Return the URL to redirect the user to for OAuth consent."""

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        """Exchange an authorization code for a UserInfo struct."""

    def desktop_url(self, redirect_uri: str, state: str) -> str:
        """Return the authorization URL for the desktop OAuth flow.
        Default: same as authorization_url. Override for provider-specific params.
        """
        return self.authorization_url(redirect_uri, state)

    def __repr__(self) -> str:
        return f"<AuthProvider {self.name!r}>"
