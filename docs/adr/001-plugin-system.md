# ADR-001: Feed Plugin System

**Status:** Implemented  
**Date:** 2026-07

## Context

Feed parsing was a 487-line monolith in `feed_parser.py` with `if is_youtube:` branches
throughout. Adding a new feed type (GitHub, Reddit, Substack) required editing core files.

## Decision

Introduce a `FeedPlugin` ABC in `app/plugins/`. Each feed type is an isolated module that
implements `can_handle()`, `fetch()`, and optionally `normalize_url()`, `search()`, `discover()`.

A `PluginRegistry` singleton dispatches:
- `get_fetch_plugin(url)` → first plugin where `can_handle(url)` is True
- `get_search_plugin(source_id)` → plugin that owns the search source
- `list_search_sources()` → all `SearchSourceMeta` from all plugins

## Consequences

- Adding a feed type = one new file + one `register()` call. Zero router changes.
- `feed_parser.py` is now 100 lines (thin DB-write layer only).
- `search.py` dropped from 663 → 222 lines.
- All third-party API calls live inside plugins, never in routers.
