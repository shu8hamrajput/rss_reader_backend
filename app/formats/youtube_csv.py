"""YouTube subscriptions CSV importer (Google Takeout format). See ADR-004."""
from __future__ import annotations

import csv
import io

from .base import FeedImporter, ImportedFeed


class YouTubeCSVImporter(FeedImporter):
    name       = "youtube_csv"
    mime_types = ["text/csv"]
    extensions = [".csv"]

    async def parse(self, content: bytes) -> list[ImportedFeed]:
        try:
            text = content.decode("utf-8-sig")  # strip BOM
        except UnicodeDecodeError:
            raise ValueError("File must be UTF-8 encoded CSV")

        reader = csv.DictReader(io.StringIO(text))
        feeds: list[ImportedFeed] = []
        for row in reader:
            norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
            channel_id = norm.get("channel id") or norm.get("channelid") or ""
            title      = norm.get("channel title") or norm.get("channeltitle") or None

            if not channel_id.startswith("UC"):
                continue
            feeds.append(ImportedFeed(
                url      = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                title    = title,
                category = "YouTube",
            ))
        return feeds
