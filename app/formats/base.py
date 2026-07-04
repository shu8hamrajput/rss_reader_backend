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

    @abstractmethod
    async def parse(self, content: bytes) -> list[ImportedFeed]:
        """Parse raw bytes into a list of feeds to import."""

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
