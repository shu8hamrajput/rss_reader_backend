"""
The Hindu Opinion fetcher.

The Hindu Opinion RSS article URLs (https://www.thehindu.com/opinion/*.ece).
Uses Hindu-specific content selectors and a realistic browser User-Agent to
extract the article body reliably. Falls back to the default scraper if none
of the specific selectors match.
"""

import httpx
from bs4 import BeautifulSoup

from ._default import fetch as default_fetch

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}

_HINDU_SELECTORS = (
    "[itemprop='articleBody']",
    ".articlebodycontent",
    ".article",
)


def _extract(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    for selector in _HINDU_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 200:
            return str(el)
    return None


async def fetch(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        content = _extract(resp.text)
        if content:
            return content
        return await default_fetch(url)
    except Exception:
        return None
