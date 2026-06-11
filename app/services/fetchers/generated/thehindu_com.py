"""www.thehindu.com fetcher — heuristic mode, generated 2026-06-11T12:22:10.868957+00:00.
Heuristic picked content selectors: '.articlebodycontent' (3/3 samples, avg 3170 chars), "[itemprop='articleBody']" (3/3 samples, avg 2833 chars). Noise selectors found within top match: .related-topics, [class*='related'].
"""
import httpx

from app.services.fetchers._common import strip_and_select
from app.services.fetchers._default import fetch as default_fetch

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}

_DOMAIN_PATTERN = 'thehindu\\.com/'  # _registry uses pattern.search(url)

_CONTENT_SELECTORS = ('.articlebodycontent', "[itemprop='articleBody']")
_NOISE_SELECTORS = ('.related-topics', "[class*='related']")

_META = {'domain': 'www.thehindu.com', 'feed_url': 'http://www.thehindu.com/opinion/editorial/?service=rss', 'sample_urls': ['https://www.thehindu.com/opinion/editorial/sport-during-a-war-on-the-fifa-world-cup-2026/article71084874.ece', 'https://www.thehindu.com/opinion/editorial/foreseeable-accidents-on-the-recent-industrial-accidents-in-india/article71085062.ece', 'https://www.thehindu.com/opinion/editorial/new-and-raw-on-nepal-india-ties/article71081368.ece'], 'mode': 'heuristic', 'model': None, 'reasoning': 'Heuristic picked content selectors: \'.articlebodycontent\' (3/3 samples, avg 3170 chars), "[itemprop=\'articleBody\']" (3/3 samples, avg 2833 chars). Noise selectors found within top match: .related-topics, [class*=\'related\'].', 'generated_at': '2026-06-11T12:22:10.868957+00:00', 'iteration': 1}


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
