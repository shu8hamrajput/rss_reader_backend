import httpx
from bs4 import BeautifulSoup


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.thehindu.com/",
}

# Ordered from most specific to least — first match with enough text wins
_CONTENT_SELECTORS = (
    "[itemprop='articleBody']",
    ".article-body-text",
    ".content-body",
    ".article-body",
    "article",
)

# Elements to strip before extraction
_NOISE_SELECTORS = (
    ".paywall",
    ".subscription-prompt",
    ".also-read",
    ".related-topics",
    ".social-share-bar",
    ".article-tags",
    ".comments-section",
    ".more-stories",
    ".newsletter-signup",
    ".ad-container",
    "[class*='advertisement']",
)


def _extract(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    for selector in _NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    for selector in _CONTENT_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 100:
            return str(el)

    return None

if __name__ == "__main__":
    test_url = "https://www.thehindu.com/opinion/editorial/india-and-the-quad/article67141419.ece"
    import requests

    resp = requests.get(test_url, headers=_HEADERS)
    content = _extract(resp.text)
    print(content)