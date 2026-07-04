"""
Enricher registry — runs after plugin.fetch() returns ParsedArticle structs.

Registration order = execution order. See ADR-002.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ArticleEnricher
from .full_content import FullContentEnricher
from .transcript import TranscriptEnricher

if TYPE_CHECKING:
    from ..plugins.base import ParsedArticle

logger = logging.getLogger(__name__)

_FETCH_SEMAPHORE = asyncio.Semaphore(5)


class EnricherRegistry:
    def __init__(self) -> None:
        self._enrichers: list[ArticleEnricher] = []

    def register(self, enricher: ArticleEnricher) -> None:
        self._enrichers.append(enricher)
        logger.debug("Registered enricher: %s", enricher.name)

    async def run(self, articles: list["ParsedArticle"], plugin_name: str) -> list["ParsedArticle"]:
        """Run all enrichers concurrently over all articles.

        Each enricher processes all articles in parallel; enrichers run sequentially
        so later enrichers can see results from earlier ones.
        """
        for enricher in self._enrichers:
            tasks = [enricher.enrich(a, plugin_name, _FETCH_SEMAPHORE) for a in articles]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning("Enricher %s failed for article %s: %s", enricher.name, articles[i].guid, result)
                    else:
                        articles[i] = result
            except Exception as exc:
                logger.error("Enricher %s batch failed: %s", enricher.name, exc)
        return articles


enricher_registry = EnricherRegistry()

# Register in pipeline order
enricher_registry.register(TranscriptEnricher())   # transcripts before full_content (avoids overwrite)
enricher_registry.register(FullContentEnricher())

__all__ = ["ArticleEnricher", "EnricherRegistry", "enricher_registry"]
