"""
In-process event bus. See ADR-003.

Usage:
    # Producer (router/task) — knows nothing about consumers
    await event_bus.emit("article.created", {"article_id": 42, ...})

    # Consumer (handler) — registered here, isolated from routers
    @event_bus.on("article.created")
    async def my_handler(payload: dict) -> None: ...
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class EventBus:
    """Lightweight asyncio event bus with isolated handler errors."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str) -> Callable[[Handler], Handler]:
        """Decorator: @event_bus.on("article.created")"""
        def decorator(fn: Handler) -> Handler:
            self._handlers[event].append(fn)
            logger.debug("Registered handler %s for event %r", fn.__name__, event)
            return fn
        return decorator

    async def emit(self, event: str, payload: dict) -> None:
        """Fire all handlers for `event` concurrently. Errors are isolated per handler."""
        handlers = self._handlers.get(event, [])
        if not handlers:
            return
        results = await asyncio.gather(
            *[h(payload) for h in handlers],
            return_exceptions=True,
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error("Event handler %s failed for %r: %s", handler.__name__, event, result)


event_bus = EventBus()

# Import handlers to trigger registration via @event_bus.on decorators
from . import handlers  # noqa: E402, F401

__all__ = ["EventBus", "event_bus"]
