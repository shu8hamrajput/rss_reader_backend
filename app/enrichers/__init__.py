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

_CONCURRENCY = 5  # max simultaneous outbound HTTP calls across all enrichers


class EnricherRegistry:
    def __init__(self) -> None:
        self._enrichers: list[ArticleEnricher] = []
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Created lazily inside a running event loop — safe on all Python versions
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(_CONCURRENCY)
        return self._semaphore

    def register(self, enricher: ArticleEnricher) -> None:
        self._enrichers.append(enricher)
        logger.debug("Registered enricher: %s", enricher.name)

    async def run(self, articles: list["ParsedArticle"], plugin_name: str) -> list["ParsedArticle"]:
        """Run all enrichers sequentially; articles within each enricher run concurrently."""
        sem = self._get_semaphore()
        for enricher in self._enrichers:
            tasks = [enricher.enrich(a, plugin_name, sem) for a in articles]
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
