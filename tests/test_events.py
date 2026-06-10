import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.events import event_stream, publish


class _FakePubSub:
    def __init__(self, messages):
        self._messages = messages
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()
        self.aclose = AsyncMock()

    async def listen(self):
        for message in self._messages:
            yield message


async def _collect(user_id):
    return [event async for event in event_stream(user_id)]


def test_publish_calls_redis_publish():
    with patch("app.services.events.redis_client.publish") as mock_publish:
        publish(5, {"type": "new_articles", "feed_id": 1, "count": 3})

    mock_publish.assert_called_once_with(
        "sse:user:5", json.dumps({"type": "new_articles", "feed_id": 1, "count": 3})
    )


def test_event_stream_yields_parsed_message():
    messages = [
        {"type": "message", "data": json.dumps({"type": "new_articles", "feed_id": 1, "count": 2})},
    ]
    fake_pubsub = _FakePubSub(messages)

    with patch("app.services.events.async_redis_client.pubsub", return_value=fake_pubsub):
        events = asyncio.run(_collect(7))

    assert events == [{"type": "new_articles", "feed_id": 1, "count": 2}]
    fake_pubsub.subscribe.assert_called_once_with("sse:user:7")
    fake_pubsub.unsubscribe.assert_called_once_with("sse:user:7")
    fake_pubsub.aclose.assert_called_once()


def test_event_stream_skips_non_message_types():
    messages = [
        {"type": "subscribe", "data": 1},
        {"type": "message", "data": json.dumps({"type": "ping"})},
    ]
    fake_pubsub = _FakePubSub(messages)

    with patch("app.services.events.async_redis_client.pubsub", return_value=fake_pubsub):
        events = asyncio.run(_collect(7))

    assert events == [{"type": "ping"}]


def test_event_stream_skips_invalid_json():
    messages = [
        {"type": "message", "data": "not-json"},
        {"type": "message", "data": json.dumps({"type": "ping"})},
    ]
    fake_pubsub = _FakePubSub(messages)

    with patch("app.services.events.async_redis_client.pubsub", return_value=fake_pubsub):
        events = asyncio.run(_collect(7))

    assert events == [{"type": "ping"}]
