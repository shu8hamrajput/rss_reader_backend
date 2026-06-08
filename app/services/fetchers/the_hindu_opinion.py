"""
The Hindu Opinion fetcher.

The Hindu Opinion RSS article URLs (https://www.thehindu.com/opinion/*.ece).
Uses Hindu-specific content selectors and a realistic browser User-Agent to
extract the article body reliably. Falls back to the default scraper if none
of the specific selectors match.
"""

from ._default import fetch as default_fetch


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

if __name__ == "__main__":
    import asyncio

    url = "https://www.thehindu.com/opinion/editorial/india-and-the-quad/article67141419.ece"
    content = asyncio.run(fetch(url))
    print(content)