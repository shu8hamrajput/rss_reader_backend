"""OPML import and export format. See ADR-004."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from .base import FeedExporter, FeedImporter, ImportedFeed
from ..models import Feed, User


def _iter_outlines(element: ET.Element, folder: str | None = None):
    for outline in element.findall("outline"):
        xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
        title   = outline.get("title") or outline.get("text")
        feed_type = (outline.get("type") or "").lower()
        if xml_url and feed_type in ("rss", "atom", ""):
            yield xml_url.strip(), title, folder
        else:
            yield from _iter_outlines(outline, title or outline.get("text"))


class OPMLImporter(FeedImporter):
    name       = "opml"
    mime_types = ["application/xml", "text/xml", "text/x-opml"]
    extensions = [".opml", ".xml"]

    async def parse(self, content: bytes) -> list[ImportedFeed]:
        self._check_size(content)
        try:
            root = DefusedET.fromstring(content.decode("utf-8", errors="replace"))
        except (ET.ParseError, DefusedXmlException) as exc:
            raise ValueError(f"Invalid OPML XML: {exc}")
        body = root.find("body")
        if body is None:
            raise ValueError("OPML has no <body> element")
        return [
            ImportedFeed(url=url, title=title, category=folder)
            for url, title, folder in _iter_outlines(body)
        ]


class OPMLExporter(FeedExporter):
    name         = "opml"
    content_type = "application/xml"
    extension    = ".opml"

    async def export(self, feeds: list[Feed], user: User) -> bytes:
        root = ET.Element("opml", version="1.0")
        head = ET.SubElement(root, "head")
        ET.SubElement(head, "title").text = f"{user.name or user.email}'s feeds"
        ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )
        body = ET.SubElement(root, "body")

        # Group by category
        from collections import defaultdict
        by_cat: dict[str | None, list[Feed]] = defaultdict(list)
        for feed in feeds:
            cats = feed.categories or []
            if cats:
                for cat in cats:
                    by_cat[cat.name].append(feed)
            else:
                by_cat[None].append(feed)

        written: set[int] = set()
        for cat_name, cat_feeds in by_cat.items():
            if cat_name:
                folder = ET.SubElement(body, "outline", text=cat_name, title=cat_name)
            else:
                folder = body
            for feed in cat_feeds:
                if feed.id in written:
                    continue
                written.add(feed.id)
                attrs = {"type": "rss", "text": feed.title or feed.url,
                         "title": feed.title or feed.url, "xmlUrl": feed.url}
                if feed.site_url:
                    attrs["htmlUrl"] = feed.site_url
                ET.SubElement(folder, "outline", **attrs)

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
        return xml_str.encode("utf-8")
