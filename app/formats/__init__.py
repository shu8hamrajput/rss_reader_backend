"""
Import/Export format registry. See ADR-004.

Adding a new import format (Pocket, Instapaper, etc.):
  1. Create app/formats/my_format.py implementing FeedImporter
  2. format_registry.register_importer(MyFormatImporter())
  3. Done — /opml/import auto-detects it by MIME type or extension.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import FeedExporter, FeedImporter, ImportResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class FormatRegistry:
    def __init__(self) -> None:
        self._importers: list[FeedImporter] = []
        self._exporters: dict[str, FeedExporter] = {}

    def register_importer(self, importer: FeedImporter) -> None:
        self._importers.append(importer)
        logger.debug("Registered importer: %s", importer.name)

    def register_exporter(self, exporter: FeedExporter) -> None:
        self._exporters[exporter.name] = exporter
        logger.debug("Registered exporter: %s", exporter.name)

    def get_importer(self, filename: str, content_type: str = "") -> FeedImporter | None:
        """Find the right importer by filename extension or MIME type."""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        for imp in self._importers:
            if ext in imp.extensions or content_type in imp.mime_types:
                return imp
        return None

    def get_exporter(self, name: str) -> FeedExporter | None:
        return self._exporters.get(name)

    def list_importers(self) -> list[dict]:
        return [{"name": i.name, "extensions": i.extensions, "mime_types": i.mime_types} for i in self._importers]

    def list_exporters(self) -> list[dict]:
        return [{"name": e.name, "content_type": e.content_type, "extension": e.extension} for e in self._exporters.values()]


format_registry = FormatRegistry()

# Register built-in formats
from .opml import OPMLImporter, OPMLExporter
from .youtube_csv import YouTubeCSVImporter
from .markdown import MarkdownExporter

format_registry.register_importer(OPMLImporter())
format_registry.register_importer(YouTubeCSVImporter())
format_registry.register_exporter(OPMLExporter())
format_registry.register_exporter(MarkdownExporter())

__all__ = ["FeedImporter", "FeedExporter", "ImportResult", "FormatRegistry", "format_registry"]
