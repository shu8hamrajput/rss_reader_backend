"""
Celery tasks — periodic feed refresh (replaces the old in-process APScheduler job with a Celery-beat-driven schedule).

Runs in a separate worker process on its own DB session; the existing async
fetch/parse pipeline is driven via asyncio.run(). New-article counts are
published to Redis so connected SSE clients still get live updates.
"""
import asyncio
import logging
from collections import defaultdict

from .celery_app import celery_app
from .database import SessionLocal
from .models import Feed
from .services.events import publish
from .services.feed_parser import refresh_url_for_all_subscribers

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.refresh_all_feeds")
def refresh_all_feeds() -> None:
    """Refresh every active feed, grouped by URL so shared feeds are fetched once."""
    db = SessionLocal()
    try:
        feeds = db.query(Feed).filter(Feed.is_active == True).all()

        url_groups: dict[str, list[Feed]] = defaultdict(list)
        for feed in feeds:
            url_groups[feed.url].append(feed)

        logger.info(
            "Celery beat: refreshing %d unique URL(s) across %d active feed(s)",
            len(url_groups), len(feeds),
        )

        for url, feed_group in url_groups.items():
            try:
                results = asyncio.run(refresh_url_for_all_subscribers(feed_group, db))
                for feed in feed_group:
                    new_count = results.get(feed.id, 0)
                    if new_count > 0:
                        publish(
                            feed.user_id,
                            {"type": "new_articles", "feed_id": feed.id, "count": new_count},
                        )
            except Exception as exc:
                logger.warning("Failed to refresh URL %s: %s", url, exc)
    finally:
        db.close()


@celery_app.task(name="app.tasks.refresh_feed_by_id")
def refresh_feed_by_id(feed_id: int) -> int:
    """Refresh a single feed by ID. Returns the new-article count."""
    db = SessionLocal()
    try:
        feed = db.query(Feed).filter(Feed.id == feed_id).first()
        if not feed:
            return 0
        results = asyncio.run(refresh_url_for_all_subscribers([feed], db))
        new_count = results.get(feed.id, 0)
        if new_count > 0:
            publish(
                feed.user_id,
                {"type": "new_articles", "feed_id": feed.id, "count": new_count},
            )
        return new_count
    finally:
        db.close()
