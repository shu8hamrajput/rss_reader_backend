import httpx

from ._common import clean_soup, strip_and_select
from ..url_safety import assert_public_url

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}

_CONTENT_SELECTORS = (
    "article",
    "main",
    "[role='main']",
    ".post-content",
    ".article-body",
    ".entry-content",
    ".story-body",
)


def extract_content(html: str) -> str | None:
    content = strip_and_select(html, _CONTENT_SELECTORS)
    if content:
        return content

    soup = clean_soup(html)
    body = soup.find("body")
    return str(body) if body else None


async def fetch(url: str) -> str | None:
    try:
        assert_public_url(url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        return extract_content(resp.text)
    except Exception:
        return None
