"""
Transcript enricher — fetches spoken-word text for audio and YouTube articles.

For podcasts: fetches VTT/SRT from podcast:transcript tag (Podcasting 2.0).
For YouTube:  fetches auto-generated transcript via timedtext API (no key needed).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import ArticleEnricher
from ..plugins.base import ParsedArticle
from ..services.url_safety import assert_public_url

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "RSSReader/1.0"}


async def _fetch_podcast_transcript(url: str) -> str | None:
    try:
        assert_public_url(url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        lines = [
            line.strip() for line in resp.text.splitlines()
            if line.strip() and line.strip() not in ("WEBVTT",)
            and "-->" not in line and not line.strip().isdigit()
        ]
        return " ".join(lines)[:100_000] if lines else None
    except Exception as exc:
        logger.debug("Podcast transcript fetch failed for %s: %s", url, exc)
        return None


async def _fetch_youtube_transcript(video_id: str) -> str | None:
    url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang=en&fmt=json3"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, headers=_HEADERS) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            return None
        data = resp.json()
        lines = [
            seg.get("utf8", "").strip().replace("\n", " ")
            for event in data.get("events", [])
            for seg in event.get("segs", [])
        ]
        text = " ".join(l for l in lines if l and l != "\n").strip()
        return text[:100_000] if text else None
    except Exception as exc:
        logger.debug("YouTube transcript fetch failed for %s: %s", video_id, exc)
        return None


class TranscriptEnricher(ArticleEnricher):
    name = "transcript"

    async def enrich(
        self,
        article: ParsedArticle,
        plugin_name: str,
        semaphore: asyncio.Semaphore,
    ) -> ParsedArticle:
        if article.full_content:
            return article  # already enriched

        # YouTube transcript
        if article.media_type == "video/youtube" and article.media_url:
            import re
            m = re.search(r"[?&]v=([\w-]{11})|/shorts/([\w-]{11})|youtu\.be/([\w-]{11})", article.media_url)
            if m:
                video_id = m.group(1) or m.group(2) or m.group(3)
                async with semaphore:
                    article.full_content = await _fetch_youtube_transcript(video_id)
            return article

        # Podcast transcript (stored in tags as "transcript:<url>")
        if article.media_type and article.media_type.startswith("audio/"):
            transcript_url = next(
                (t.removeprefix("transcript:") for t in (article.tags or []) if t.startswith("transcript:")),
                None,
            )
            if transcript_url:
                async with semaphore:
                    article.full_content = await _fetch_podcast_transcript(transcript_url)

        return article
