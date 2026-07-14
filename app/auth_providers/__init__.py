"""
Auth provider registry. See ADR-005.

Adding a new OAuth provider (GitHub, Apple, etc.):
  1. Create app/auth_providers/github.py implementing AuthProvider
  2. provider_registry.register(GitHubAuthProvider())
  3. The generic /auth/{provider} route handles the rest automatically.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import AuthProvider, UserInfo

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AuthProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AuthProvider] = {}

    def register(self, provider: AuthProvider) -> None:
        self._providers[provider.name] = provider
        logger.debug("Registered auth provider: %s", provider.name)

    def get(self, name: str) -> AuthProvider | None:
        return self._providers.get(name)

    def list_providers(self) -> list[dict]:
        return [
            {"name": p.name, "display_name": p.display_name, "icon": p.icon}
            for p in self._providers.values()
        ]


provider_registry = AuthProviderRegistry()

from .google import GoogleAuthProvider
from .github import GitHubAuthProvider
from ..config import settings

provider_registry.register(GoogleAuthProvider())

# GitHub is optional — only register when credentials are configured
if settings.github_client_id and settings.github_client_secret:
    provider_registry.register(GitHubAuthProvider())

__all__ = ["AuthProvider", "UserInfo", "AuthProviderRegistry", "provider_registry"]
