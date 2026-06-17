"""
Fetcher registry — maps URL hostname patterns to async fetcher callables.

A fetcher is any coroutine function with the signature:
    async def fetch(url: str) -> str | None

Call `register(pattern, fetcher)` to add a custom fetcher for a URL pattern.
`fetch_content(url)` picks the first matching fetcher, falling back to the
default BeautifulSoup fetcher when no pattern matches.
"""
import importlib.util
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

ContentFetcher = Callable[[str], Awaitable[str | None]]

_registry: list[tuple[re.Pattern, ContentFetcher]] = []


def register(pattern: str, fetcher: ContentFetcher) -> None:
    """Register a custom fetcher for URLs matching *pattern* (searched, not full-match)."""
    _registry.append((re.compile(pattern), fetcher))


def register_from_path(path: Path) -> bool:
    """Dynamically import an approved generated-fetcher module and register it —
    in-process hot-reload, no restart needed."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pattern = getattr(module, "_DOMAIN_PATTERN", None)
    fetch = getattr(module, "fetch", None)
    if pattern and fetch:
        register(pattern, fetch)
        return True
    return False


def unregister(pattern: str) -> None:
    """Drop a previously-registered fetcher for *pattern* (matched against the
    compiled pattern's source) — used before re-registering on approve."""
    global _registry
    _registry = [(p, f) for p, f in _registry if p.pattern != pattern]


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
