"""
Background scheduler — refreshes all active feeds every 30 minutes.
Feeds sharing the same URL are fetched once and applied to all subscribers.
Publishes SSE events so connected clients get live new-article counts.
"""
import asyncio
import logging
from collections import defaultdict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..database import SessionLocal
from ..models import Feed
from .feed_parser import refresh_url_for_all_subscribers
from .events import publish

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def _refresh_all_feeds() -> None:
    db = SessionLocal()
    try:
        feeds = db.query(Feed).filter(Feed.is_active == True).all()

        url_groups: dict[str, list[Feed]] = defaultdict(list)
        for feed in feeds:
            url_groups[feed.url].append(feed)

        logger.info(
            "Scheduler: refreshing %d unique URL(s) across %d active feed(s)",
            len(url_groups), len(feeds),
        )

        for url, feed_group in url_groups.items():
            try:
                results = await refresh_url_for_all_subscribers(feed_group, db)
                for feed in feed_group:
                    new_count = results.get(feed.id, 0)
                    if new_count > 0:
                        await publish(
                            feed.user_id,
                            {"type": "new_articles", "feed_id": feed.id, "count": new_count},
                        )
            except Exception as exc:
                logger.warning("Failed to refresh URL %s: %s", url, exc)
    finally:
        db.close()


def start() -> None:
    scheduler.add_job(
        _refresh_all_feeds,
        trigger=IntervalTrigger(minutes=30),
        id="refresh_all_feeds",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started — feeds refresh every 30 minutes")


def stop() -> None:
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
