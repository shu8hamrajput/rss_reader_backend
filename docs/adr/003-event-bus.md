# ADR-003: In-Process Event Bus

**Status:** Implemented  
**Date:** 2026-07

## Context

Webhooks, SSE notifications, and future integrations (email digest, Slack, push) were
fired imperatively from scattered routers. `articles.py` called `fire_webhooks()` directly.
Every new integration required touching router code.

## Decision

Introduce a lightweight in-process `EventBus` in `app/bus/`. Routers emit named events;
handlers are registered independently. The bus is not a message queue — it is synchronous
fire-and-forget within the same process, suitable for the current single-process Fly.io deployment.

```python
# Router (producer) — knows nothing about consumers
await event_bus.emit("article.created", {"article_id": article.id, "feed_id": feed.id, ...})

# Handler (consumer) — registered in app/bus/handlers/
@event_bus.on("article.created")
async def fire_webhooks(payload: dict) -> None: ...

@event_bus.on("article.created")
async def push_sse(payload: dict) -> None: ...
```

## Events

| Event | Payload fields |
|---|---|
| `article.created` | article_id, feed_id, user_id, title, url |
| `article.read` | article_id, user_id |
| `highlight.created` | highlight_id, article_id, user_id |
| `feed.added` | feed_id, user_id, plugin_name |
| `feed.refresh.done` | feed_id, new_count |

## Consequences

- Routers are decoupled from notification infrastructure.
- Adding a new integration = one new handler file + one `@event_bus.on()` decorator.
- Handlers run concurrently via `asyncio.gather()` with individual error isolation.
- If a handler raises, the bus logs and continues — other handlers are unaffected.
- For cross-process or persistent events (e.g. after migrating to multiple instances),
  replace the bus with Redis pub/sub without changing any producer or consumer code.
