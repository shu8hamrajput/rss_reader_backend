"""
Google News fetcher.

Google News RSS article URLs (news.google.com/rss/articles/...) are redirect
wrappers around the real article. This fetcher resolves the redirect chain and
scrapes the final destination page. If the redirect still lands on google.com
(e.g. AMP viewer), it falls back to the canonical <link> tag in the page.
"""
import httpx
from urllib.parse import urlparse

from ._default import _HEADERS, extract_content


async def fetch(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()

        final_url = str(resp.url)

        # Happy path: redirect landed us on the real article
        if "google.com" not in urlparse(final_url).netloc:
            return extract_content(resp.text)

        # Still on Google (AMP viewer, consent page, etc.) — look for canonical
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        canonical_tag = soup.find("link", rel="canonical")
        canonical_url = canonical_tag.get("href") if canonical_tag else None

        if canonical_url and "google.com" not in urlparse(canonical_url).netloc:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                resp2 = await client.get(canonical_url, headers=_HEADERS)
                resp2.raise_for_status()
            return extract_content(resp2.text)

        return None
    except Exception:
        return None
