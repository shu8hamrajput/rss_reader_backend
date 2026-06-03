import httpx
from bs4 import BeautifulSoup


async def fetch_full_content(url: str) -> str | None:
    """Fetch and extract the main readable HTML content from an article URL."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"},
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        for candidate in ("article", "main", "[role='main']", ".post-content", ".article-body"):
            el = soup.select_one(candidate)
            if el and len(el.get_text(strip=True)) > 200:
                return str(el)

        body = soup.find("body")
        return str(body) if body else None
    except Exception:
        return None
