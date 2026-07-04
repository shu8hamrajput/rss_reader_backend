"""
Full-content enricher — fetches readable article HTML via BeautifulSoup.

Runs for non-podcast, non-YouTube articles that have a URL.
Skips if full_content is already set (e.g. transcript was pre-fetched).
"""
from __future__ import annotations

import asyncio
import logging

from .base import ArticleEnricher
from ..plugins.base import ParsedArticle
from ..services.article_fetcher import fetch_full_content

logger = logging.getLogger(__name__)


class FullContentEnricher(ArticleEnricher):
    name = "full_content"

    async def enrich(
        self,
        article: ParsedArticle,
        plugin_name: str,
        semaphore: asyncio.Semaphore,
    ) -> ParsedArticle:
        # Skip podcasts, YouTube, and articles without a URL
        if not article.url:
            return article
        if article.media_type == "video/youtube":
            return article
        if article.media_type and article.media_type.startswith("audio/"):
            return article
        if article.full_content:
            return article

        async with semaphore:
            try:
                article.full_content = await fetch_full_content(article.url)
            except Exception as exc:
                logger.debug("full_content fetch failed for %s: %s", article.url, exc)
        return article
