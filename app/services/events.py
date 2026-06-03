"""
In-memory pub/sub event bus for SSE.

The scheduler publishes new-article events; SSE clients subscribe per user.
No persistence — events are lost if the server restarts or no client is connected.
"""
import asyncio
from collections import defaultdict
from typing import AsyncGenerator

_queues: dict[int, list[asyncio.Queue]] = defaultdict(list)


def subscribe(user_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _queues[user_id].append(q)
    return q


def unsubscribe(user_id: int, q: asyncio.Queue) -> None:
    try:
        _queues[user_id].remove(q)
    except ValueError:
        pass
    if not _queues[user_id]:
        del _queues[user_id]


async def publish(user_id: int, event: dict) -> None:
    for q in list(_queues.get(user_id, [])):
        await q.put(event)


async def event_stream(user_id: int) -> AsyncGenerator[dict, None]:
    q = subscribe(user_id)
    try:
        while True:
            event = await q.get()
            yield event
    finally:
        unsubscribe(user_id, q)
