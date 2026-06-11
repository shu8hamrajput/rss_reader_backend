"""Free heuristic selector proposer — DOM/text-density scoring, no external calls."""
from app.services.fetchers._common import clean_soup

from .proposal import SelectorProposal

# Ordered by general specificity/reliability; earlier entries win ties.
_CANDIDATE_CONTENT_SELECTORS = (
    "[itemprop='articleBody']",
    ".articlebodycontent",
    ".article-body",
    ".post-content",
    ".entry-content",
    ".story-body",
    "article",
    ".article",
    ".content",
    "#content",
    "main",
    "[role='main']",
)

# From app/providers/the_hindu_opinion.py (legacy reference template).
_CANDIDATE_NOISE_SELECTORS = (
    ".related-topics",
    ".related-articles",
    ".also-read",
    ".more-stories",
    ".newsletter-signup",
    ".social-share-bar",
    ".comments-section",
    ".article-tags",
    ".paywall",
    ".subscription-prompt",
    "[class*='advertisement']",
    "[class*='related']",
)

_MIN_CHARS = 200
_MAX_CONTENT_SELECTORS = 4
_MAX_NOISE_SELECTORS = 8


def propose_selectors(html_samples: list[str]) -> SelectorProposal:
    if not html_samples:
        raise ValueError("html_samples must not be empty")

    soups = [clean_soup(html) for html in html_samples]

    scored: list[tuple[str, int, float, int]] = []
    for index, selector in enumerate(_CANDIDATE_CONTENT_SELECTORS):
        match_count = 0
        total_len = 0
        for soup in soups:
            el = soup.select_one(selector)
            if el:
                length = len(el.get_text(strip=True))
                if length > _MIN_CHARS:
                    match_count += 1
                    total_len += length
        if match_count >= 1:
            scored.append((selector, match_count, total_len / match_count, index))

    scored.sort(key=lambda item: (-item[1], -item[2], item[3]))
    top = scored[:_MAX_CONTENT_SELECTORS]
    content_selectors = [selector for selector, _count, _avg, _index in top]

    noise_selectors: list[str] = []
    if top:
        best_selector = top[0][0]
        for noise_selector in _CANDIDATE_NOISE_SELECTORS:
            if len(noise_selectors) >= _MAX_NOISE_SELECTORS:
                break
            for soup in soups:
                el = soup.select_one(best_selector)
                if el and el.select(noise_selector):
                    noise_selectors.append(noise_selector)
                    break

    if top:
        picks = ", ".join(
            f"{selector!r} ({count}/{len(html_samples)} samples, avg {int(avg)} chars)"
            for selector, count, avg, _index in top
        )
        reasoning = f"Heuristic picked content selectors: {picks}."
        if noise_selectors:
            reasoning += f" Noise selectors found within top match: {', '.join(noise_selectors)}."
    else:
        reasoning = f"Heuristic found no selector matching > {_MIN_CHARS} chars in any sample."

    return SelectorProposal(
        content_selectors=content_selectors,
        noise_selectors=noise_selectors,
        reasoning=reasoning,
    )
