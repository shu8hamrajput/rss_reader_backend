"""
Article enricher protocol.

Enrichers run after plugin.fetch() returns ParsedArticle structs.
Plugins are pure parsers — they extract data from the feed XML only.
Enrichers handle side-effects: HTTP fetches, AI calls, external APIs.

See ADR-002.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..plugins.base import ParsedArticle


class ArticleEnricher(ABC):
    """Base class for all article enrichers."""

    name: str

    @abstractmethod
    async def enrich(
        self,
        article: "ParsedArticle",
        plugin_name: str,
        semaphore: asyncio.Semaphore,
    ) -> "ParsedArticle":
        """Enrich a single article in-place and return it.

        `plugin_name` lets enrichers be conditional — e.g. TranscriptEnricher
        only runs for plugin_name="youtube" or when media_type starts with "audio/".
        `semaphore` must be acquired before making any outbound HTTP call.
        """

    def __repr__(self) -> str:
        return f"<ArticleEnricher {self.name!r}>"
