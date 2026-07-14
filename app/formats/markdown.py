"""Markdown exporter — exports feeds as a readable Markdown list. See ADR-004."""
from __future__ import annotations

from .base import FeedExporter
from ..models import Feed, User


class MarkdownExporter(FeedExporter):
    name         = "markdown"
    content_type = "text/markdown"
    extension    = ".md"

    async def export(self, feeds: list[Feed], user: User) -> bytes:
        lines = [f"# {user.name or user.email}'s Feed List\n"]
        from collections import defaultdict
        by_cat: dict[str, list[Feed]] = defaultdict(list)
        uncategorised: list[Feed] = []
        for feed in feeds:
            cats = feed.categories or []
            if cats:
                for cat in cats:
                    by_cat[cat.name].append(feed)
            else:
                uncategorised.append(feed)

        for cat_name, cat_feeds in sorted(by_cat.items()):
            lines.append(f"\n## {cat_name}\n")
            for feed in cat_feeds:
                title = feed.title or feed.url
                url   = feed.site_url or feed.url
                lines.append(f"- [{title}]({url}) — `{feed.url}`")

        if uncategorised:
            lines.append("\n## Uncategorised\n")
            for feed in uncategorised:
                title = feed.title or feed.url
                url   = feed.site_url or feed.url
                lines.append(f"- [{title}]({url}) — `{feed.url}`")

        return "\n".join(lines).encode("utf-8")
