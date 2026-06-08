"""
Pub/sub event bus for SSE, backed by Redis.

The Celery refresh task publishes new-article events; SSE clients subscribe
per user. Using Redis (instead of an in-process queue) means events reach
subscribers regardless of which app instance — or the Celery worker —
produced them, and survive an individual app worker restarting.
"""
import json
from typing import AsyncGenerator

from ..redis_client import async_redis_client, redis_client

_CHANNEL_PREFIX = "sse:user:"


def _channel(user_id: int) -> str:
    return f"{_CHANNEL_PREFIX}{user_id}"


def publish(user_id: int, event: dict) -> None:
    """Synchronous publish — used by Celery tasks (sync worker context)."""
    redis_client.publish(_channel(user_id), json.dumps(event))


async def event_stream(user_id: int) -> AsyncGenerator[dict, None]:
    pubsub = async_redis_client.pubsub()
    await pubsub.subscribe(_channel(user_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (TypeError, ValueError):
                continue
    finally:
        await pubsub.unsubscribe(_channel(user_id))
        await pubsub.aclose()
