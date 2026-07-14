"""
Celery application — Redis broker, Redis result backend.

Run a worker with:   celery -A app.celery_app worker --loglevel=info
Run the beat with:   celery -A app.celery_app beat --loglevel=info

Pass -B to the worker (celery -A app.celery_app worker -B) to run the beat
scheduler embedded in the worker process — used in production to cover both
roles with a single Fly machine.
"""
from celery import Celery

from .config import settings

celery_app = Celery(
    "rss_reader",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "refresh-all-feeds": {
            "task": "app.tasks.refresh_all_feeds",
            "schedule": 30 * 60,  # every 30 minutes — matches the old APScheduler interval
        },
        "prune-expired-articles": {
            "task": "app.tasks.prune_expired_articles",
            "schedule": 24 * 60 * 60,  # once a day — retention is a slow-moving setting, no need for finer cadence
        },
    },
)
