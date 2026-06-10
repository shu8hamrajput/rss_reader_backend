import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.fetchers import _default, _google_news, _registry, the_hindu_opinion

from .conftest import _FakeAsyncClient, _response


# ── _google_news ──────────────────────────────────────────────────────────────

def test_google_news_fetch_decodes_and_delegates_to_default():
    with patch("googlenewsdecoder.new_decoderv1", return_value={"status": True, "decoded_url": "https://real.example/article"}), \
            patch("app.services.fetchers._google_news.default_fetch", new=AsyncMock(return_value="article body")) as mock_default:
        result = asyncio.run(_google_news.fetch("https://news.google.com/rss/articles/abc"))

    assert result == "article body"
    mock_default.assert_called_once_with("https://real.example/article")


def test_google_news_fetch_status_false_returns_none():
    with patch("googlenewsdecoder.new_decoderv1", return_value={"status": False}):
        result = asyncio.run(_google_news.fetch("https://news.google.com/rss/articles/abc"))

    assert result is None


def test_google_news_fetch_decoder_exception_returns_none():
    with patch("googlenewsdecoder.new_decoderv1", side_effect=Exception("boom")):
        result = asyncio.run(_google_news.fetch("https://news.google.com/rss/articles/abc"))

    assert result is None


# ── the_hindu_opinion ─────────────────────────────────────────────────────────

_HINDU_URL = "https://www.thehindu.com/opinion/editorial/foo/article1.ece"


def test_the_hindu_opinion_fetch_extracts_article_body():
    html = "<html><body><div itemprop='articleBody'>" + ("Lorem ipsum dolor sit amet. " * 20) + "</div></body></html>"
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(text=html, status_code=200)))

    with patch("app.services.fetchers.the_hindu_opinion.httpx.AsyncClient", return_value=fake), \
            patch("app.services.fetchers.the_hindu_opinion.default_fetch", new=AsyncMock()) as mock_default:
        result = asyncio.run(the_hindu_opinion.fetch(_HINDU_URL))

    assert result is not None
    assert "Lorem ipsum" in result
    mock_default.assert_not_called()


def test_the_hindu_opinion_fetch_falls_back_to_default():
    html = "<html><body><div>Just a short snippet</div></body></html>"
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(text=html, status_code=200)))

    with patch("app.services.fetchers.the_hindu_opinion.httpx.AsyncClient", return_value=fake), \
            patch("app.services.fetchers.the_hindu_opinion.default_fetch", new=AsyncMock(return_value="fallback content")) as mock_default:
        result = asyncio.run(the_hindu_opinion.fetch(_HINDU_URL))

    assert result == "fallback content"
    mock_default.assert_called_once_with(_HINDU_URL)


def test_the_hindu_opinion_fetch_http_error_returns_none():
    error_resp = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_resp),
    )))

    with patch("app.services.fetchers.the_hindu_opinion.httpx.AsyncClient", return_value=fake):
        result = asyncio.run(the_hindu_opinion.fetch(_HINDU_URL))

    assert result is None


# ── _registry ─────────────────────────────────────────────────────────────────

def test_registry_resolves_google_news():
    assert _registry._resolve("https://news.google.com/rss/articles/abc") is _google_news.fetch


def test_registry_resolves_the_hindu_opinion():
    assert _registry._resolve(_HINDU_URL) is the_hindu_opinion.fetch


def test_registry_falls_back_to_default():
    assert _registry._resolve("https://example.com/article") is _default.fetch
