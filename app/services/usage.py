"""
Redis-backed per-user daily usage counters.

Meters full-content scraping — the cost driver that scales directly with
individual user actions (refetch, bulk save-later) rather than with feed or
article volume. Counters are keyed by UTC date and expire automatically so
no cleanup job is needed.
"""
from datetime import datetime, timezone

from ..models import User
from ..redis_client import redis_client
from .plans import effective_plan, limits_for

_TTL_SECONDS = 26 * 60 * 60  # outlives a UTC day so "today"'s key is always valid


def _fetch_usage_key(user_id: int) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"usage:full_content_fetch:{user_id}:{day}"


def remaining_fetch_quota(user: User) -> int | None:
    """Remaining full-content fetches *user* may make today, or None if unlimited."""
    limit = limits_for(effective_plan(user)).daily_full_content_fetches
    if limit is None:
        return None
    used = int(redis_client.get(_fetch_usage_key(user.id)) or 0)
    return max(0, limit - used)


def record_fetches(user: User, count: int) -> None:
    """Record *count* full-content fetches against today's quota for *user*."""
    if count <= 0 or limits_for(effective_plan(user)).daily_full_content_fetches is None:
        return
    key = _fetch_usage_key(user.id)
    pipe = redis_client.pipeline()
    pipe.incrby(key, count)
    pipe.expire(key, _TTL_SECONDS)
    pipe.execute()
