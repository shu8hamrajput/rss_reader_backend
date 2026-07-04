"""
Webhook handler — fires user webhooks on application events.
Consumers of: article.created, alert.matched, feed.added.
"""
from __future__ import annotations

import json
import logging

import httpx

from .. import event_bus
from ...database import SessionLocal
from ...models import UserWebhook

logger = logging.getLogger(__name__)


async def _deliver(wh: UserWebhook, event: str, payload: dict) -> None:
    try:
        subscribed = json.loads(wh.events) if isinstance(wh.events, str) else (wh.events or [])
    except Exception:
        return
    if event not in subscribed:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                wh.url,
                json={"event": event, "payload": payload},
                headers={"Content-Type": "application/json", "User-Agent": "RSSReader-Webhook/1.0"},
            )
        # Update last_fired_at in a separate session (fire-and-forget)
        with SessionLocal() as db:
            hook = db.get(UserWebhook, wh.id)
            if hook:
                from datetime import datetime, timezone
                hook.last_fired_at = datetime.now(timezone.utc)
                db.commit()
        logger.debug("Webhook %d fired for %r → HTTP %s", wh.id, event, resp.status_code)
    except Exception as exc:
        logger.warning("Webhook %d delivery failed for %r: %s", wh.id, event, exc)


async def _fire_for_user(user_id: int, event: str, payload: dict) -> None:
    with SessionLocal() as db:
        webhooks = db.query(UserWebhook).filter(
            UserWebhook.user_id == user_id,
            UserWebhook.is_active == True,  # noqa: E712
        ).all()
    import asyncio
    await asyncio.gather(*[_deliver(wh, event, payload) for wh in webhooks])


@event_bus.on("article.created")
async def on_article_created(payload: dict) -> None:
    await _fire_for_user(payload["user_id"], "new_article", payload)


@event_bus.on("alert.matched")
async def on_alert_matched(payload: dict) -> None:
    await _fire_for_user(payload["user_id"], "alert_matched", payload)


@event_bus.on("feed.added")
async def on_feed_added(payload: dict) -> None:
    await _fire_for_user(payload["user_id"], "feed_added", payload)
