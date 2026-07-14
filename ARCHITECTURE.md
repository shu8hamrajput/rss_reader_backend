# Backend Architecture

## Core principle

> The main web server is a thin orchestration layer.  
> All type-specific behaviour lives in plugins.

The backend has two distinct layers:

```
┌─────────────────────────────────────────────────────┐
│  Core (app/)                                        │
│  HTTP routing · Auth · DB · Tasks · Rate-limiting   │
│  Does NOT know what a YouTube video or podcast is   │
└──────────────────┬──────────────────────────────────┘
                   │ delegates to
┌──────────────────▼──────────────────────────────────┐
│  Plugins (app/plugins/)                             │
│  YouTubePlugin · GitHubPlugin · FeedlyPlugin · …   │
│  All third-party API calls live here                │
└─────────────────────────────────────────────────────┘
```

---

## What belongs in core

| Concern | Location |
|---|---|
| HTTP routing, request validation | `app/routers/` |
| Auth (JWT, Google/GitHub OAuth) | `app/routers/auth.py`, `app/auth.py`, `app/auth_providers/` |
| Database models and migrations | `app/models.py`, `app/main.py:_migrate()` |
| Feed CRUD (subscribe, list, delete) | `app/routers/feeds.py` |
| Custom fetcher generation (LLM-assisted) | `app/routers/fetchers.py` |
| Article storage and retrieval | `app/routers/articles.py` |
| Highlights, notes, tags | `app/routers/highlights.py` |
| Article routing rules | `app/routers/rules.py` |
| Saved search alerts | `app/routers/alerts.py` |
| AI briefings/digests | `app/routers/briefings.py` |
| Collections, OPML/export, webhooks, preferences, feature votes | `app/routers/collections.py`, `opml.py`, `export.py`, `webhooks.py`, `preferences.py`, `feature_votes.py` |
| Celery task scheduling (the runner) | `app/tasks.py` |
| Rate limiting, CORS, health check | `app/main.py` |

Beyond the feed-source plugin layer described below, three sibling pipelines follow the
same "core dispatches, plugin implements" pattern — see their ADRs for design rationale:

| Layer | Location | ADR |
|---|---|---|
| Feed source plugins | `app/plugins/` | ADR-001 |
| Content enrichers (full-content, transcripts) | `app/enrichers/` | ADR-002 |
| Event bus (e.g. webhook delivery) | `app/bus/` | ADR-003 |
| Import/export formats (OPML, YouTube CSV, Markdown) | `app/formats/` | ADR-004 |
| OAuth providers (Google, GitHub) | `app/auth_providers/` | ADR-005 |

**Rule:** Routers dispatch to plugins. They never contain `httpx.get("https://api.feedly.com/…")` or any other third-party call.

---

## What belongs in plugins

| Concern | Location |
|---|---|
| How to fetch and parse a feed URL | `plugin.fetch()` |
| How to convert a user URL to a feed URL | `plugin.normalize_url()` |
| How to search a feed directory | `plugin.search()` |
| How to discover feeds on a website | `plugin.discover()` |
| All third-party HTTP calls | inside the plugin that owns them |

---

## Plugin anatomy

```
app/plugins/
  __init__.py   # registers all plugins in priority order
  base.py       # FeedPlugin ABC, ParsedFeed, ParsedArticle, SearchSourceMeta
  registry.py   # PluginRegistry singleton
  youtube.py    # YouTubePlugin
  github.py     # GitHubPlugin
  feedly.py     # FeedlyPlugin
  podcast.py    # PodcastPlugin
  default.py    # DefaultPlugin (RSS/Atom fallback)
```

### Plugin interface

```python
class FeedPlugin(ABC):
    # Identity
    name: str           # slug, stored in feeds.plugin_name
    display_name: str
    description: str
    icon_emoji: str

    # Search sources this plugin exposes (can be multiple)
    search_sources: list[SearchSourceMeta] = []

    # Required for fetch plugins
    def can_handle(self, url: str) -> bool: ...
    async def fetch(self, url, etag, last_modified) -> tuple[ParsedFeed | None, int]: ...

    # Optional
    def normalize_url(self, url: str) -> str: ...          # URL → canonical feed URL
    async def search(self, query, source_id, **kw) -> list[SearchResult]: ...
    async def discover(self, url: str) -> list[DiscoveredFeed]: ...
```

### Two dispatch axes

```
Feed URL  →  plugin_registry.get_fetch_plugin(url)   →  plugin.fetch()
Search    →  plugin_registry.get_search_plugin(id)   →  plugin.search()
Discover  →  each plugin.discover() tried in order   →  first non-empty wins
```

---

## How routing works

### Feed fetch (`/feeds`, Celery tasks)

```
POST /feeds  →  feeds.py
  plugin = registry.get_fetch_plugin(url)
  url    = plugin.normalize_url(url)       # e.g. @handle → feeds/videos.xml
  feed_parser.refresh_feed(feed, db)
    → plugin.fetch(url, etag, last_modified)
    → write ParsedFeed to DB
```

### Search (`/search/feeds`)

```
GET /search/feeds?q=python&source=feedly  →  search.py
  plugin = registry.get_search_plugin("feedly")   # FeedlyPlugin
  results = await plugin.search(q, source_id="feedly")
  return FeedSearchResponse(results)
```

### Discovery (`/search/discover`)

```
GET /search/discover?url=youtube.com/@fireship  →  search.py
  for plugin in registry.all_plugins:
    feeds = await plugin.discover(url)    # YouTubePlugin returns early
    if feeds: return feeds
  # fallback: generic HTML <link rel="alternate"> scraping
```

### Search index listing (`/search/indexes`)

```
GET /search/indexes  →  search.py
  registry.list_search_sources()   # aggregated from all plugin.search_sources
  return [{id, name, description, icon, …}]
```

---

## Adding a plugin

### New feed type (e.g. Substack)

```python
# app/plugins/substack.py
from .base import FeedPlugin, ParsedFeed

class SubstackPlugin(FeedPlugin):
    name         = "substack"
    display_name = "Substack"
    description  = "Substack newsletters via RSS"
    icon_emoji   = "📝"

    def can_handle(self, url: str) -> bool:
        return "substack.com" in url

    def normalize_url(self, url: str) -> str:
        # substack.com/@author → substack.com/feed
        if not url.endswith("/feed"):
            return url.rstrip("/") + "/feed"
        return url

    async def fetch(self, url, etag, last_modified):
        # fetch RSS, return ParsedFeed
        ...
```

```python
# app/plugins/__init__.py  — add one line:
plugin_registry.register(SubstackPlugin())  # before DefaultPlugin
```

Done. No changes to routers, tasks, or any core file.

### New search source (e.g. Listen Notes)

```python
# app/plugins/listennotes.py
class ListenNotesPlugin(FeedPlugin):
    name = "listennotes"
    search_sources = [
        SearchSourceMeta(
            id="listennotes", name="Listen Notes",
            description="Podcast search engine", category="podcast",
            icon="🎵", placeholder="e.g. AI, startup",
            requires_key=True, requires_key_hint="Free key at listennotes.com/api",
        )
    ]
    def can_handle(self, url): return False
    async def fetch(self, *_): raise NotImplementedError
    async def search(self, query, source_id, limit=20, **kw):
        # call Listen Notes API
        ...
```

```python
plugin_registry.register(ListenNotesPlugin())
```

The `/search/indexes` endpoint automatically includes it. The frontend picks it up dynamically. No other changes.

---

## What never goes in routers

| ❌ Wrong — in router | ✅ Right — in plugin |
|---|---|
| `httpx.get("https://cloud.feedly.com/…")` | `FeedlyPlugin.search()` |
| `if "youtube.com" in url: …` | `YouTubePlugin.can_handle()` |
| `feedparser.parse(resp.text)` | `DefaultPlugin.fetch()` |
| `re.search(r'"channelId"…', html)` | `YouTubePlugin.resolve_url()` |
| `hashlib.sha256(key + secret + ts)` | `PodcastPlugin._podcast_index()` |

The only external calls a router ever makes are to the plugin registry and the database.

---

## Current plugin map

| Plugin | Fetches | Search sources |
|---|---|---|
| `YouTubePlugin` | `youtube.com/feeds/videos.xml` | `youtube` |
| `GitHubPlugin` | `github.com/**/*.atom` | `github` |
| `DefaultPlugin` | everything else (RSS/Atom + podcasts) | — |
| `FeedlyPlugin` | — | `feedly` |
| `PodcastPlugin` | — | `itunes`, `podcast_index`, `gpodder`, `fyyd` |

Total search sources: **7** (feedly, youtube, itunes, podcast_index, gpodder, fyyd, github)  
All sourced from `plugin_registry.list_search_sources()` — frontend needs no hardcoding.
