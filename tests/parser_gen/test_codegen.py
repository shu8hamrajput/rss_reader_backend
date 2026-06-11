import ast

import pytest

from app.services.parser_gen import codegen
from app.services.parser_gen.proposal import SelectorProposal


def test_slug_for_domain_strips_www_and_normalizes():
    assert codegen.slug_for_domain("www.example.com") == "example_com"
    assert codegen.slug_for_domain("news.ycombinator.com") == "news_ycombinator_com"


def test_domain_pattern_escapes_dots_and_strips_www():
    assert codegen.domain_pattern("www.example.com") == r"example\.com/"


def _meta(**overrides):
    meta = {
        "domain": "example.com",
        "feed_url": "https://example.com/feed.xml",
        "sample_urls": ["https://example.com/a1", "https://example.com/a2"],
        "mode": "heuristic",
        "model": None,
        "reasoning": "Heuristic picked content selectors: 'article' (2/2 samples, avg 500 chars).",
        "generated_at": "2026-06-11T12:00:00+00:00",
        "iteration": 1,
    }
    meta.update(overrides)
    return meta


@pytest.fixture()
def fetchers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(codegen, "_FETCHERS_DIR", tmp_path)
    (tmp_path / "generated" / "candidates").mkdir(parents=True)
    return tmp_path


def test_render_module_produces_valid_python():
    proposal = SelectorProposal(
        content_selectors=["article", ".content"],
        noise_selectors=[".related-articles"],
        reasoning="Heuristic picked content selectors: 'article' (2/2 samples, avg 500 chars).",
    )

    source = codegen.render_module(proposal, _meta(), r"example\.com/")

    ast.parse(source)
    assert "_CONTENT_SELECTORS = ('article', '.content')" in source
    assert "_NOISE_SELECTORS = ('.related-articles',)" in source
    assert "LLM-assisted" not in source


def test_render_module_llm_mode_label_includes_model():
    proposal = SelectorProposal(content_selectors=["article"], noise_selectors=[], reasoning="r")

    source = codegen.render_module(proposal, _meta(mode="llm", model="claude-sonnet-4-6"), r"example\.com/")

    ast.parse(source)
    assert "LLM-assisted (claude-sonnet-4-6)" in source


def test_write_load_round_trip_with_empty_selectors(fetchers_dir):
    proposal = SelectorProposal(content_selectors=[], noise_selectors=[], reasoning="nothing found")

    source = codegen.render_module(proposal, _meta(reasoning="nothing found"), r"example\.com/")
    path = codegen.write_candidate("example_com", source)

    assert path == fetchers_dir / "generated" / "candidates" / "example_com.py"

    attrs = codegen.load_module_attrs(path)
    assert attrs["content_selectors"] == ()
    assert attrs["noise_selectors"] == ()
    assert attrs["domain_pattern"] == r"example\.com/"
    assert attrs["meta"]["domain"] == "example.com"
    assert attrs["meta"]["sample_urls"] == ["https://example.com/a1", "https://example.com/a2"]


def test_approve_moves_candidate_to_active(fetchers_dir):
    proposal = SelectorProposal(content_selectors=["article"], noise_selectors=[], reasoning="r")
    source = codegen.render_module(proposal, _meta(), r"example\.com/")
    codegen.write_candidate("example_com", source)

    active = codegen.approve("example_com")

    assert active == fetchers_dir / "generated" / "example_com.py"
    assert active.exists()
    assert not codegen.candidate_path("example_com").exists()


def test_approve_missing_candidate_raises(fetchers_dir):
    with pytest.raises(FileNotFoundError):
        codegen.approve("does_not_exist")
