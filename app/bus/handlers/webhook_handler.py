"""
Webhook handler — fires user webhooks on application events.
Consumers of: article.created, alert.matched, feed.added.

Uses a single DB session per emit call:
  1. Fetch all active webhooks for the user
  2. Deliver concurrently via httpx
  3. Batch-update last_fired_at for successfully delivered hooks
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from .. import event_bus
from ...database import SessionLocal
from ...models import UserWebhook

logger = logging.getLogger(__name__)


async def _deliver(webhook_id: int, url: str, subscribed: list[str], event: str, payload: dict) -> bool:
    """Deliver one webhook. Returns True if delivered (for batch last_fired_at update)."""
    if event not in subscribed:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"event": event, "payload": payload},
                headers={"Content-Type": "application/json", "User-Agent": "RSSReader-Webhook/1.0"},
            )
        logger.debug("Webhook %d fired for %r → HTTP %s", webhook_id, event, resp.status_code)
        return True
    except Exception as exc:
        logger.warning("Webhook %d delivery failed for %r: %s", webhook_id, event, exc)
        return False


async def _fire_for_user(user_id: int, event: str, payload: dict) -> None:
    # Single session: fetch webhooks, deliver, batch-update last_fired_at
    with SessionLocal() as db:
        webhooks = db.query(UserWebhook).filter(
            UserWebhook.user_id == user_id,
            UserWebhook.is_active == True,  # noqa: E712
        ).all()
        if not webhooks:
            return

        # Snapshot mutable fields before async delivery (session is not thread-safe across awaits)
        targets = []
        for wh in webhooks:
            try:
                subscribed = json.loads(wh.events) if isinstance(wh.events, str) else (wh.events or [])
            except Exception:
                subscribed = []
            targets.append((wh.id, wh.url, subscribed))

    # Deliver outside the session (httpx is async, session is sync)
    results = await asyncio.gather(
        *[_deliver(wid, url, sub, event, payload) for wid, url, sub in targets],
        return_exceptions=True,
    )

    # Batch-update last_fired_at for successfully delivered hooks in one trip
    now = datetime.now(timezone.utc)
    fired_ids = [
        targets[i][0]
        for i, ok in enumerate(results)
        if ok is True
    ]
    if fired_ids:
        with SessionLocal() as db:
            db.query(UserWebhook).filter(UserWebhook.id.in_(fired_ids)).update(
                {"last_fired_at": now}, synchronize_session=False
            )
            db.commit()


@event_bus.on("article.created")
async def on_article_created(payload: dict) -> None:
    # webhook_eligible=False opts a specific feed out of instant webhook push —
    # SSE live-notification and alert matching (separate event-bus consumers) are unaffected.
    if not payload.get("webhook_eligible", True):
        return
    await _fire_for_user(payload["user_id"], "new_article", payload)


@event_bus.on("alert.matched")
async def on_alert_matched(payload: dict) -> None:
    await _fire_for_user(payload["user_id"], "alert_matched", payload)


@event_bus.on("feed.added")
async def on_feed_added(payload: dict) -> None:
    await _fire_for_user(payload["user_id"], "feed_added", payload)
