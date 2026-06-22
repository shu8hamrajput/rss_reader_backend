"""nypost.com fetcher — heuristic mode, generated 2026-06-11T21:06:27.302105+00:00.
Heuristic picked content selectors: '#content' (1/1 samples, avg 3137 chars), 'main' (1/1 samples, avg 3137 chars), 'article' (1/1 samples, avg 3044 chars), '.entry-content' (1/1 samples, avg 2846 chars).
"""
import httpx

from app.services.fetchers._common import strip_and_select
from app.services.fetchers._default import fetch as default_fetch

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}

_DOMAIN_PATTERN = 'nypost\\.com/'  # _registry uses pattern.search(url)

_CONTENT_SELECTORS = ('#content', 'main', 'article', '.entry-content')
_NOISE_SELECTORS = ()

_META = {'domain': 'nypost.com', 'feed_url': None, 'sample_urls': ['https://nypost.com/2026/06/11/opinion/kamar-samuels-swims-waist-deep-in-nyc-schools-corrupt-waste/'], 'mode': 'heuristic', 'model': None, 'reasoning': "Heuristic picked content selectors: '#content' (1/1 samples, avg 3137 chars), 'main' (1/1 samples, avg 3137 chars), 'article' (1/1 samples, avg 3044 chars), '.entry-content' (1/1 samples, avg 2846 chars).", 'generated_at': '2026-06-11T21:06:27.302105+00:00', 'iteration': 1}


def _extract(html: str) -> str | None:
    if not _CONTENT_SELECTORS:
        return None
    return strip_and_select(html, _CONTENT_SELECTORS, _NOISE_SELECTORS)


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
