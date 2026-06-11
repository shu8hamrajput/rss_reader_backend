"""LLM-assisted selector proposer — LlamaIndex + Anthropic Claude.

Manually triggered via `--llm`. Requires `ANTHROPIC_API_KEY` (app.config.settings).
"""
from llama_index.core.prompts import PromptTemplate
from llama_index.llms.anthropic import Anthropic

from app.config import settings
from app.services.fetchers._common import clean_soup

from .proposal import SelectorProposal

_MAX_SAMPLE_CHARS = 60_000

_PROMPT_TEMPLATE = PromptTemplate(
    "You are configuring a content extractor for a news/article website.\n"
    "Given the cleaned HTML of {sample_count} sample article page(s) from the same\n"
    "site, propose CSS selectors for a Python BeautifulSoup-based extractor.\n"
    "\n"
    "- `content_selectors`: 1-4 CSS selectors, ordered as a fallback chain (the\n"
    "  first selector that matches and yields substantial text wins). Prefer\n"
    "  stable, semantic selectors (e.g. `article`, `[itemprop='articleBody']`,\n"
    "  `.entry-content`) over hashed/auto-generated class names.\n"
    "- `noise_selectors`: CSS selectors for elements to remove from *within* the\n"
    "  matched content element — ads, related-article widgets, newsletter\n"
    "  signup prompts, social-share bars, comment sections, and similar noise.\n"
    "  Only include selectors you have direct evidence for in the samples below.\n"
    "- `reasoning`: a short explanation of your choices.\n"
    "{current_section}"
    "{feedback_section}"
    "{hint_section}"
    "\n"
    "Samples:\n"
    "{samples}\n"
)


def _render_samples(html_samples: list[str]) -> str:
    parts = []
    for i, html in enumerate(html_samples, start=1):
        cleaned = str(clean_soup(html))
        if len(cleaned) > _MAX_SAMPLE_CHARS:
            cleaned = cleaned[:_MAX_SAMPLE_CHARS] + "\n<!-- truncated -->"
        parts.append(f"--- Sample {i} ---\n{cleaned}")
    return "\n\n".join(parts)


def propose_selectors(
    html_samples: list[str],
    current: SelectorProposal | None = None,
    feedback: str | None = None,
    hint: SelectorProposal | None = None,
) -> SelectorProposal:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — add it to .env for --llm mode, or omit --llm."
        )

    current_section = ""
    if current is not None:
        current_section = (
            "\nThe extractor currently uses:\n"
            f"  content_selectors = {current.content_selectors!r}\n"
            f"  noise_selectors = {current.noise_selectors!r}\n"
            f"  reasoning = {current.reasoning!r}\n"
            "Improve on this based on the samples and any feedback below.\n"
        )

    feedback_section = ""
    if feedback:
        feedback_section = (
            f"\nUser feedback on the current extraction: {feedback!r}\n"
            "Address this feedback specifically in your updated selectors.\n"
        )

    hint_section = ""
    if hint is not None:
        hint_section = (
            "\nA free heuristic pass over the same samples suggests as a starting point:\n"
            f"  content_selectors = {hint.content_selectors!r}\n"
            f"  noise_selectors = {hint.noise_selectors!r}\n"
            "Verify and improve on this — it is only a hint, not a requirement.\n"
        )

    llm = Anthropic(model=settings.parser_gen_model, api_key=settings.anthropic_api_key, max_tokens=4096)
    return llm.structured_predict(
        SelectorProposal,
        _PROMPT_TEMPLATE,
        sample_count=len(html_samples),
        current_section=current_section,
        feedback_section=feedback_section,
        hint_section=hint_section,
        samples=_render_samples(html_samples),
    )
