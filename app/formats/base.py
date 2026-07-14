"""Base classes for feed import/export formats. See ADR-004."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Feed, User
    from sqlalchemy.orm import Session


@dataclass
class ImportedFeed:
    url: str
    title: str | None = None
    category: str | None = None   # folder/category name from the source


@dataclass
class ImportResult:
    added: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class FeedImporter(ABC):
    name: str
    mime_types: list[str] = []
    extensions: list[str] = []
    max_bytes: int = 5 * 1024 * 1024  # subclasses can override

    def sniff(self, content: bytes) -> bool:
        """Return True if this importer can handle this specific content.

        Called after extension/MIME match to disambiguate (e.g. two importers
        share the .csv extension but target different formats).
        Default: always True (extension match is sufficient).
        """
        return True

    @abstractmethod
    async def parse(self, content: bytes) -> list[ImportedFeed]:
        """Parse raw bytes into a list of feeds to import.

        Implementations should call _check_size(content) first.
        """

    def _check_size(self, content: bytes) -> None:
        if len(content) > self.max_bytes:
            raise ValueError(f"File too large: {len(content)} bytes (max {self.max_bytes})")

    def __repr__(self) -> str:
        return f"<FeedImporter {self.name!r}>"


class FeedExporter(ABC):
    name: str
    content_type: str
    extension: str   # ".opml", ".md"

    @abstractmethod
    async def export(self, feeds: list["Feed"], user: "User") -> bytes:
        """Serialise feeds into bytes for download."""

    def __repr__(self) -> str:
        return f"<FeedExporter {self.name!r}>"
