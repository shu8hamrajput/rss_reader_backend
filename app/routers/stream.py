"""
Server-Sent Events endpoint.

Clients connect once and receive a live stream of new-article notifications
as the periodic Celery refresh task processes feeds.

Event format (JSON):
  {"type": "new_articles", "feed_id": 42, "count": 7}
  {"type": "ping"}           — keepalive every 25 s
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ..auth import get_current_user
from ..models import User
from ..services.events import event_stream

router = APIRouter(prefix="/stream", tags=["Stream"])

_PING_INTERVAL = 25  # seconds


async def _generate(request: Request, user_id: int):
    async def inner():
        stream = event_stream(user_id)
        ping_task = asyncio.create_task(_ping_forever())

        try:
            async for event in stream:
                if await request.is_disconnected():
                    break
                yield {"data": json.dumps(event)}
        finally:
            ping_task.cancel()

    async def _ping_forever():
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            # EventSourceResponse comment line keeps the connection alive
            yield  # not used directly — we inject pings via a separate queue approach below

    # Simpler: merge event stream with periodic pings
    async def merged():
        q = asyncio.Queue()

        async def _feed_events():
            async for ev in event_stream(user_id):
                await q.put(ev)

        async def _pings():
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                await q.put({"type": "ping"})

        feed_task = asyncio.create_task(_feed_events())
        ping_task = asyncio.create_task(_pings())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    continue
        finally:
            feed_task.cancel()
            ping_task.cancel()

    return merged()


@router.get(
    "/articles",
    summary="SSE stream of new-article events",
    description=(
        "Opens a Server-Sent Events connection. "
        "The server pushes a JSON event whenever new articles arrive for any of your feeds:\n\n"
        "```\n"
        'data: {"type": "new_articles", "feed_id": 42, "count": 7}\n'
        "```\n\n"
        "A `ping` event is sent every 25 seconds to keep the connection alive. "
        "Reconnect automatically on disconnect using the EventSource API."
    ),
    response_class=EventSourceResponse,
)
async def article_stream(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return EventSourceResponse(await _generate(request, current_user.id))
