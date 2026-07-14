# ADR-002: Article Enricher Pipeline

**Status:** Implemented  
**Date:** 2026-07

## Context

Article enrichment (full-text fetch, transcript extraction, readability scoring) was
hardcoded inside each plugin's `fetch()` method. `DefaultPlugin` called `fetch_full_content()`;
`YouTubePlugin` called `_fetch_transcript()`. Adding a new enrichment step (AI tagging,
translation, keyword extraction) required editing plugin code.

## Decision

Introduce an `ArticleEnricher` ABC in `app/enrichers/`. Plugins return raw `ParsedArticle`
structs with only data extracted from the feed itself. After `plugin.fetch()` returns,
`feed_parser.py` runs each registered enricher sequentially.

```
plugin.fetch(url)
  → ParsedFeed (raw, unenriched)
    → FullContentEnricher    (fetches full article HTML)
    → TranscriptEnricher     (fetches podcast/YouTube transcripts)
    → [any future enricher]
      → write to DB
```

Enrichers are registered in `app/enrichers/__init__.py`. Each enricher receives
`(article: ParsedArticle, feed_plugin_name: str)` and returns the modified article.
Enrichers can be conditional — `TranscriptEnricher` only runs for audio/youtube articles.

## Interface

```python
class ArticleEnricher(ABC):
    name: str
    async def enrich(
        self,
        article: ParsedArticle,
        plugin_name: str,
        semaphore: asyncio.Semaphore,
    ) -> ParsedArticle: ...
```

## Consequences

- Plugins are pure parsers. No HTTP calls for side-effects inside `fetch()`.
- Adding enrichment = one new file + one `enricher_registry.register()` call.
- Enrichers are independently testable without mocking plugin internals.
- The pipeline is explicit and ordered — easier to debug than scattered `asyncio.gather()` calls.
