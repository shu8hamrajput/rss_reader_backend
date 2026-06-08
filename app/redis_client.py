"""
Shared Redis clients.

`redis_client`       — synchronous client, used by Celery tasks and the rate limiter.
`async_redis_client` — asyncio client, used by the FastAPI app (SSE pub/sub).
"""
import redis
import redis.asyncio as redis_asyncio

from .config import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)
async_redis_client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
