"""
Fetcher registry — maps URL hostname patterns to async fetcher callables.

A fetcher is any coroutine function with the signature:
    async def fetch(url: str) -> str | None

Call `register(pattern, fetcher)` to add a custom fetcher for a URL pattern.
`fetch_content(url)` picks the first matching fetcher, falling back to the
default BeautifulSoup fetcher when no pattern matches.
"""
import re
from collections.abc import Awaitable, Callable

ContentFetcher = Callable[[str], Awaitable[str | None]]

_registry: list[tuple[re.Pattern, ContentFetcher]] = []


def register(pattern: str, fetcher: ContentFetcher) -> None:
    """Register a custom fetcher for URLs matching *pattern* (searched, not full-match)."""
    _registry.append((re.compile(pattern), fetcher))


def _resolve(url: str) -> ContentFetcher:
    for pattern, fetcher in _registry:
        if pattern.search(url):
            return fetcher
    from . import _default
    return _default.fetch


async def fetch_content(url: str) -> str | None:
    return await _resolve(url)(url)


# ── Built-in registrations ────────────────────────────────────────────────────

from . import _google_news  # noqa: E402

register(r"news\.google\.com", _google_news.fetch)

# ── Generated registrations (app.services.parser_gen) ─────────────────────────

from . import generated  # noqa: E402,F401
