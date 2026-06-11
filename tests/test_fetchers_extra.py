import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.fetchers import _default, _google_news, _registry, generated
from app.services.fetchers.generated import thehindu_com

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


# ── generated/thehindu_com ───────────────────────────────────────────────────

_HINDU_URL = "https://www.thehindu.com/opinion/editorial/foo/article1.ece"


def test_thehindu_com_fetch_extracts_article_body():
    html = "<html><body><div itemprop='articleBody'>" + ("Lorem ipsum dolor sit amet. " * 20) + "</div></body></html>"
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(text=html, status_code=200)))

    with patch("app.services.fetchers.generated.thehindu_com.httpx.AsyncClient", return_value=fake), \
            patch("app.services.fetchers.generated.thehindu_com.default_fetch", new=AsyncMock()) as mock_default:
        result = asyncio.run(thehindu_com.fetch(_HINDU_URL))

    assert result is not None
    assert "Lorem ipsum" in result
    mock_default.assert_not_called()


def test_thehindu_com_fetch_falls_back_to_default():
    html = "<html><body><div>Just a short snippet</div></body></html>"
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(text=html, status_code=200)))

    with patch("app.services.fetchers.generated.thehindu_com.httpx.AsyncClient", return_value=fake), \
            patch("app.services.fetchers.generated.thehindu_com.default_fetch", new=AsyncMock(return_value="fallback content")) as mock_default:
        result = asyncio.run(thehindu_com.fetch(_HINDU_URL))

    assert result == "fallback content"
    mock_default.assert_called_once_with(_HINDU_URL)


def test_thehindu_com_fetch_http_error_returns_none():
    error_resp = MagicMock(status_code=500)
    fake = _FakeAsyncClient(get=AsyncMock(return_value=_response(
        raise_exc=httpx.HTTPStatusError("error", request=MagicMock(), response=error_resp),
    )))

    with patch("app.services.fetchers.generated.thehindu_com.httpx.AsyncClient", return_value=fake):
        result = asyncio.run(thehindu_com.fetch(_HINDU_URL))

    assert result is None


# ── _registry ─────────────────────────────────────────────────────────────────

def test_registry_resolves_google_news():
    assert _registry._resolve("https://news.google.com/rss/articles/abc") is _google_news.fetch


def test_registry_resolves_generated_thehindu_com():
    assert _registry._resolve(_HINDU_URL) is thehindu_com.fetch


def test_registry_falls_back_to_default():
    assert _registry._resolve("https://example.com/article") is _default.fetch


# ── generated/ discovery ────────────────────────────────────────────────────────

def test_generated_discovery_skips_candidates_and_registers_active_modules():
    gen_dir = Path(generated.__file__).parent
    module_path = gen_dir / "_zzz_pytest_temp.py"
    candidate_path = gen_dir / "candidates" / "_zzz_pytest_temp_candidate.py"

    module_path.write_text(
        "_DOMAIN_PATTERN = r'zzz-pytest-temp\\.example/'\n"
        "async def fetch(url):\n"
        "    return 'temp-module'\n"
    )
    candidate_path.write_text(
        "_DOMAIN_PATTERN = r'zzz-pytest-temp-candidate\\.example/'\n"
        "async def fetch(url):\n"
        "    return 'temp-candidate'\n"
    )

    registry_snapshot = list(_registry._registry)

    try:
        importlib.reload(generated)

        resolved = _registry._resolve("https://zzz-pytest-temp.example/article")
        assert asyncio.run(resolved("https://zzz-pytest-temp.example/article")) == "temp-module"

        assert _registry._resolve("https://zzz-pytest-temp-candidate.example/article") is _default.fetch
    finally:
        module_path.unlink(missing_ok=True)
        candidate_path.unlink(missing_ok=True)
        sys.modules.pop("app.services.fetchers.generated._zzz_pytest_temp", None)
        sys.modules.pop("app.services.fetchers.generated.candidates._zzz_pytest_temp_candidate", None)
        _registry._registry[:] = registry_snapshot
