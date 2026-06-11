import pytest

from app.services.parser_gen import heuristics

_LONG = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 20
_MEDIUM = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 6

_SAMPLE_WITH_ARTICLE_BODY = (
    "<html><body>"
    f"<article>{_LONG}<div class='related-articles'>Related stories</div></article>"
    f"<div class='content'>{_MEDIUM}</div>"
    f"<div class='article-body'>{_LONG}</div>"
    "</body></html>"
)

_SAMPLE_WITHOUT_ARTICLE_BODY = (
    "<html><body>"
    f"<article>{_LONG}</article>"
    f"<div class='content'>{_MEDIUM}</div>"
    "</body></html>"
)


def test_propose_selectors_ranks_by_match_count_then_avg_length():
    proposal = heuristics.propose_selectors([_SAMPLE_WITH_ARTICLE_BODY, _SAMPLE_WITHOUT_ARTICLE_BODY])

    # "article" and ".content" match both samples; ".article-body" matches only one.
    assert proposal.content_selectors[:2] == ["article", ".content"]
    assert ".article-body" in proposal.content_selectors
    assert proposal.content_selectors.index("article") < proposal.content_selectors.index(".article-body")


def test_propose_selectors_finds_nested_noise_selector():
    proposal = heuristics.propose_selectors([_SAMPLE_WITH_ARTICLE_BODY, _SAMPLE_WITHOUT_ARTICLE_BODY])

    assert ".related-articles" in proposal.noise_selectors


def test_propose_selectors_empty_when_nothing_matches():
    html = "<html><body><div>too short</div></body></html>"

    proposal = heuristics.propose_selectors([html])

    assert proposal.content_selectors == []
    assert proposal.noise_selectors == []
    assert "no selector" in proposal.reasoning.lower()


def test_propose_selectors_raises_on_empty_samples():
    with pytest.raises(ValueError):
        heuristics.propose_selectors([])
