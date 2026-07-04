"""
SSE handler — publishes events to Redis so connected SSE clients receive live updates.
Consumers of: article.created, feed.refresh.done.
"""
from __future__ import annotations

import logging

from .. import event_bus
from ...services.events import publish as redis_publish

logger = logging.getLogger(__name__)


@event_bus.on("article.created")
async def on_article_created(payload: dict) -> None:
    try:
        redis_publish(payload["user_id"], {
            "type":     "new_article",
            "feed_id":  payload.get("feed_id"),
            "count":    payload.get("count", 1),
        })
    except Exception as exc:
        logger.warning("SSE publish failed: %s", exc)


@event_bus.on("feed.refresh.done")
async def on_feed_refresh_done(payload: dict) -> None:
    try:
        redis_publish(payload["user_id"], {
            "type":    "refresh_done",
            "feed_id": payload.get("feed_id"),
            "count":   payload.get("new_count", 0),
        })
    except Exception as exc:
        logger.warning("SSE publish failed for feed.refresh.done: %s", exc)
