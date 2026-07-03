"""
GitHub feed plugin.

Handles GitHub Atom feeds:
  - Releases:  https://github.com/{owner}/{repo}/releases.atom
  - Commits:   https://github.com/{owner}/{repo}/commits/{branch}.atom
  - Issues:    https://github.com/{owner}/{repo}/issues.atom
  - Tags:      https://github.com/{owner}/{repo}/tags.atom

URL normalisation: converts plain repo URLs to the releases feed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .base import FeedPlugin, ParsedArticle, ParsedFeed
from ..services.url_safety import assert_public_url

logger = logging.getLogger(__name__)

_GH_REPO_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)")


class GitHubPlugin(FeedPlugin):
    name         = "github"
    display_name = "GitHub"
    description  = "GitHub releases, commits, issues and tags via Atom"
    icon_emoji   = "🐙"

    def can_handle(self, url: str) -> bool:
        return bool(
            re.search(r"github\.com/.+\.(atom|rss)$", url)
            or re.search(r"github\.com/.+/(releases|commits|issues|tags)\.atom", url)
        )

    def normalize_url(self, url: str) -> str:
        """Convert a github.com repo URL to its releases Atom feed."""
        if url.endswith(".atom") or url.endswith(".rss"):
            return url
        m = _GH_REPO_RE.search(url)
        if m:
            return f"https://github.com/{m.group(1)}/releases.atom"
        return url

    async def fetch(
        self,
        url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[ParsedFeed | None, int]:
        assert_public_url(url)
        headers: dict[str, str] = {
            "User-Agent": "RSSReader/1.0",
            "Accept": "application/atom+xml, application/xml, text/xml",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code == 304:
            return None, 304

        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"GitHub feed parse error: {parsed.get('bozo_exception')}")

        feed_info = parsed.feed
        # Derive icon from GitHub favicon
        icon_url = None
        m = _GH_REPO_RE.search(url)
        if m:
            owner = m.group(1).split("/")[0]
            icon_url = f"https://avatars.githubusercontent.com/{owner}?size=64"

        result = ParsedFeed(
            title         = feed_info.get("title"),
            description   = feed_info.get("subtitle") or feed_info.get("description"),
            site_url      = feed_info.get("link"),
            icon_url      = icon_url,
            etag          = resp.headers.get("ETag"),
            last_modified = resp.headers.get("Last-Modified"),
        )

        articles: list[ParsedArticle] = []
        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not guid:
                continue

            # Releases use the tag name as episode_number
            tag_name: str | None = None
            link = entry.get("link") or ""
            m_tag = re.search(r"/releases/tag/([^/]+)$", link)
            if m_tag:
                tag_name = m_tag.group(1)

            content = None
            if entry.get("content"):
                content = entry.content[0].get("value")
            elif entry.get("summary"):
                content = entry.get("summary")

            published = _parse_date(entry)

            articles.append(ParsedArticle(
                guid           = guid,
                title          = entry.get("title"),
                url            = entry.get("link"),
                author         = entry.get("author"),
                summary        = entry.get("summary"),
                content        = content,
                published_at   = published,
                episode_number = tag_name,
            ))

        result.articles = articles
        return result, resp.status_code


def _parse_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        val = entry.get(attr)
        if val:
            try:
                return parsedate_to_datetime(val)
            except Exception:
                pass
    return None
