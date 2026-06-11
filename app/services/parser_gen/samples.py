"""Feed/article sampling helpers for the parser generator CLI.

Synchronous (httpx, not AsyncClient) — the CLI runs outside an event loop.
"""
from urllib.parse import urlparse

import feedparser
import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}


def fetch_html(url: str) -> str | None:
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def sample_article_urls(url: str, n: int) -> tuple[list[str], bool]:
    """Return (urls, is_feed). If *url* is a feed, returns up to *n* entry links."""
    parsed = feedparser.parse(url)
    if parsed.entries or parsed.get("version"):
        links = [entry.link for entry in parsed.entries if entry.get("link")]
        return links[:n], True
    return [url], False


def domain_from_url(url: str) -> str:
    return urlparse(url).netloc
