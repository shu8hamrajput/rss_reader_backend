import asyncio
from unittest.mock import AsyncMock, patch

from app.bus.handlers.webhook_handler import on_article_created


def test_on_article_created_fires_when_webhook_eligible():
    with patch("app.bus.handlers.webhook_handler._fire_for_user", new=AsyncMock()) as mock_fire:
        asyncio.run(on_article_created({"user_id": 1, "feed_id": 2, "count": 3, "webhook_eligible": True}))
    mock_fire.assert_awaited_once()


def test_on_article_created_skips_when_webhook_ineligible():
    with patch("app.bus.handlers.webhook_handler._fire_for_user", new=AsyncMock()) as mock_fire:
        asyncio.run(on_article_created({"user_id": 1, "feed_id": 2, "count": 3, "webhook_eligible": False}))
    mock_fire.assert_not_awaited()


def test_on_article_created_fires_by_default_when_flag_absent():
    """Legacy payloads without webhook_eligible (pre-migration) should still fire."""
    with patch("app.bus.handlers.webhook_handler._fire_for_user", new=AsyncMock()) as mock_fire:
        asyncio.run(on_article_created({"user_id": 1, "feed_id": 2, "count": 3}))
    mock_fire.assert_awaited_once()
