"""Shared HTML cleaning/extraction helpers.

Used by `_default.py`, every module in `generated/`, and the parser generator
(`app.services.parser_gen`) for validating proposed selectors.
"""
from bs4 import BeautifulSoup

_GENERIC_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript")


def clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_GENERIC_NOISE_TAGS):
        tag.decompose()
    return soup


def strip_and_select(
    html: str,
    content_selectors: tuple[str, ...],
    noise_selectors: tuple[str, ...] = (),
    min_chars: int = 200,
) -> str | None:
    soup = clean_soup(html)
    for selector in content_selectors:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > min_chars:
            for noise_selector in noise_selectors:
                for noise in el.select(noise_selector):
                    noise.decompose()
            return str(el)
    return None
