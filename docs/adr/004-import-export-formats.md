# ADR-004: Import/Export Format Registry

**Status:** Implemented  
**Date:** 2026-07

## Context

`opml.py` handled OPML import, OPML export, and YouTube CSV import in one 283-line file.
Adding Pocket JSON import or Readwise export required new endpoints and editing `opml.py`.

## Decision

Introduce `FeedImporter` and `FeedExporter` ABCs in `app/formats/`. Each format is an
isolated module. The `/opml/import` and `/opml/export` endpoints become generic dispatchers
that select a format handler by MIME type or file extension.

```python
class FeedImporter(ABC):
    name: str               # "opml", "youtube_csv", "pocket"
    mime_types: list[str]   # content-types this importer accepts
    extensions: list[str]   # file extensions: [".opml", ".xml"]

    @abstractmethod
    async def parse(self, content: bytes, user: User, db: Session) -> ImportResult: ...

class FeedExporter(ABC):
    name: str
    content_type: str
    extension: str

    @abstractmethod
    async def export(self, feeds: list[Feed], user: User) -> bytes: ...
```

## Format registry

```python
format_registry.register_importer(OPMLImporter())
format_registry.register_importer(YouTubeCSVImporter())
format_registry.register_exporter(OPMLExporter())
format_registry.register_exporter(MarkdownExporter())
```

## Consequences

- `opml.py` router becomes ~60 lines (format detection + dispatch).
- Adding Pocket import = one new file + one `register_importer()` call.
- Importers are independently testable.
- The `/formats` endpoint lists available importers/exporters for frontend discovery.
