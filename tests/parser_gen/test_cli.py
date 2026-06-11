from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services.parser_gen import codegen, samples
from app.services.parser_gen.__main__ import main
from app.services.parser_gen.proposal import SelectorProposal

_ARTICLE_HTML = "<html><body><article>" + ("Lorem ipsum dolor sit amet. " * 20) + "</article></body></html>"


@pytest.fixture()
def fetchers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(codegen, "_FETCHERS_DIR", tmp_path)
    (tmp_path / "generated" / "candidates").mkdir(parents=True)
    return tmp_path


def _write_candidate(slug, **meta_overrides):
    meta = {
        "domain": "example.com",
        "feed_url": None,
        "sample_urls": ["https://example.com/a1"],
        "mode": "heuristic",
        "model": None,
        "reasoning": "initial",
        "generated_at": "2026-06-11T12:00:00+00:00",
        "iteration": 1,
    }
    meta.update(meta_overrides)
    proposal = SelectorProposal(content_selectors=["article"], noise_selectors=[], reasoning="initial")
    source = codegen.render_module(proposal, meta, r"example\.com/")
    return codegen.write_candidate(slug, source)


# ── generate ─────────────────────────────────────────────────────────────────

def test_generate_heuristic_writes_candidate(fetchers_dir, monkeypatch, capsys):
    monkeypatch.setattr(samples, "sample_article_urls", lambda url, n: (["https://example.com/a1"], False))
    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    exit_code = main(["generate", "https://example.com/article"])

    assert exit_code == 0
    candidate = fetchers_dir / "generated" / "candidates" / "example_com.py"
    assert candidate.exists()

    attrs = codegen.load_module_attrs(candidate)
    assert "article" in attrs["content_selectors"]
    assert attrs["meta"]["mode"] == "heuristic"
    assert attrs["meta"]["iteration"] == 1
    assert attrs["meta"]["sample_urls"] == ["https://example.com/a1"]

    assert "Wrote candidate" in capsys.readouterr().out


def test_generate_no_fetchable_samples_exits_1(fetchers_dir, monkeypatch, capsys):
    monkeypatch.setattr(samples, "sample_article_urls", lambda url, n: (["https://example.com/a1"], False))
    monkeypatch.setattr(samples, "fetch_html", lambda url: None)

    exit_code = main(["generate", "https://example.com/article"])

    assert exit_code == 1
    assert not (fetchers_dir / "generated" / "candidates" / "example_com.py").exists()
    assert "error" in capsys.readouterr().err.lower()


def test_generate_no_article_urls_exits_1(fetchers_dir, monkeypatch, capsys):
    monkeypatch.setattr(samples, "sample_article_urls", lambda url, n: ([], True))

    exit_code = main(["generate", "https://example.com/feed.xml"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err.lower()


def test_generate_llm_without_api_key_exits_1(fetchers_dir, monkeypatch, capsys):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(samples, "sample_article_urls", lambda url, n: (["https://example.com/a1"], False))
    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    from app.services.parser_gen import llm

    mock_anthropic = MagicMock()
    monkeypatch.setattr(llm, "Anthropic", mock_anthropic)

    exit_code = main(["generate", "https://example.com/article", "--llm"])

    assert exit_code == 1
    mock_anthropic.assert_not_called()
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


# ── improvise ────────────────────────────────────────────────────────────────

def test_improvise_unknown_slug_exits_1(fetchers_dir, capsys):
    exit_code = main(["improvise", "does_not_exist"])

    assert exit_code == 1
    assert "does_not_exist" in capsys.readouterr().err


def test_improvise_bumps_iteration_and_rewrites_candidate(fetchers_dir, monkeypatch, capsys):
    _write_candidate("example_com")
    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    exit_code = main(["improvise", "example_com"])

    assert exit_code == 0
    attrs = codegen.load_module_attrs(codegen.candidate_path("example_com"))
    assert attrs["meta"]["iteration"] == 2

    out = capsys.readouterr().out
    assert "Before:" in out
    assert "After:" in out


def test_improvise_merges_new_url(fetchers_dir, monkeypatch):
    _write_candidate("example_com")
    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    exit_code = main(["improvise", "example_com", "--url", "https://example.com/a2"])

    assert exit_code == 0
    attrs = codegen.load_module_attrs(codegen.candidate_path("example_com"))
    assert attrs["meta"]["sample_urls"] == ["https://example.com/a1", "https://example.com/a2"]


def test_improvise_with_no_sample_urls_exits_1(fetchers_dir):
    _write_candidate("example_com", sample_urls=[])

    exit_code = main(["improvise", "example_com"])

    assert exit_code == 1


# ── approve ──────────────────────────────────────────────────────────────────

def test_approve_happy_path(fetchers_dir, capsys):
    _write_candidate("example_com")

    exit_code = main(["approve", "example_com"])

    assert exit_code == 0
    assert (fetchers_dir / "generated" / "example_com.py").exists()
    assert not codegen.candidate_path("example_com").exists()
    assert "Approved" in capsys.readouterr().out


def test_approve_missing_candidate_exits_1(fetchers_dir, capsys):
    exit_code = main(["approve", "does_not_exist"])

    assert exit_code == 1
    assert "does_not_exist" in capsys.readouterr().err
