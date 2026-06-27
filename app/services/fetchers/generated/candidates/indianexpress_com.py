"""indianexpress.com fetcher — heuristic mode, generated 2026-06-11T21:06:27.845662+00:00.
Heuristic picked content selectors: "[role='main']" (1/1 samples, avg 2505 chars).
"""
import httpx

from app.services.fetchers._common import strip_and_select
from app.services.fetchers._default import fetch as default_fetch

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}

_DOMAIN_PATTERN = 'indianexpress\\.com/'  # _registry uses pattern.search(url)

_CONTENT_SELECTORS = ("[role='main']",)
_NOISE_SELECTORS = ()

_META = {'domain': 'indianexpress.com', 'feed_url': None, 'sample_urls': ['https://indianexpress.com/article/opinion/editorials/in-the-fires-of-the-air-india-tragedy-a-friendship-forged-across-india-pakistan-border-10733775/'], 'mode': 'heuristic', 'model': None, 'reasoning': 'Heuristic picked content selectors: "[role=\'main\']" (1/1 samples, avg 2505 chars).', 'generated_at': '2026-06-11T21:06:27.845662+00:00', 'iteration': 1}


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
