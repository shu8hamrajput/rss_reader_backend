import pytest

from app.models import ParserRequest
from app.services.parser_gen import codegen, samples
from app.services.parser_gen.__main__ import _process_pending_requests

from ..conftest import make_article, make_feed

_ARTICLE_HTML = "<html><body><article>" + ("Lorem ipsum dolor sit amet. " * 20) + "</article></body></html>"


@pytest.fixture()
def fetchers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(codegen, "_FETCHERS_DIR", tmp_path)
    (tmp_path / "generated" / "candidates").mkdir(parents=True)
    return tmp_path


def _make_request(db_session, user, feed, **kw):
    article = make_article(db_session, feed, url=kw.pop("url", "https://example.com/articles/foo"))
    defaults = dict(
        user_id=user.id,
        article_id=article.id,
        url=article.url,
        domain="example.com",
    )
    defaults.update(kw)
    req = ParserRequest(**defaults)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_no_pending_requests(db_session, capsys):
    processed = _process_pending_requests(db_session, use_llm=False, samples_n=3)

    assert processed == 0
    assert "No pending parser requests" in capsys.readouterr().out


def test_writes_candidate_and_marks_processed(fetchers_dir, db_session, user, monkeypatch, capsys):
    feed = make_feed(db_session, user)
    req = _make_request(db_session, user, feed, note="too many ads")

    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    processed = _process_pending_requests(db_session, use_llm=False, samples_n=3)

    assert processed == 1
    candidate = fetchers_dir / "generated" / "candidates" / "example_com.py"
    assert candidate.exists()

    db_session.refresh(req)
    assert req.status == "processed"
    assert req.candidate_slug == "example_com"
    assert req.processed_at is not None

    out = capsys.readouterr().out
    assert "example.com" in out
    assert "Wrote candidate" in out


def test_groups_multiple_requests_for_same_domain(fetchers_dir, db_session, user, monkeypatch):
    feed = make_feed(db_session, user)
    req1 = _make_request(db_session, user, feed, url="https://example.com/articles/foo")
    req2 = _make_request(db_session, user, feed, url="https://example.com/articles/bar")

    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)

    processed = _process_pending_requests(db_session, use_llm=False, samples_n=3)

    assert processed == 1
    db_session.refresh(req1)
    db_session.refresh(req2)
    assert req1.status == req2.status == "processed"
    assert req1.candidate_slug == req2.candidate_slug == "example_com"


def test_unfetchable_domain_marks_failed(fetchers_dir, db_session, user, monkeypatch, capsys):
    feed = make_feed(db_session, user)
    req = _make_request(db_session, user, feed)

    monkeypatch.setattr(samples, "fetch_html", lambda url: None)

    processed = _process_pending_requests(db_session, use_llm=False, samples_n=3)

    assert processed == 0
    db_session.refresh(req)
    assert req.status == "failed"
    assert req.processed_at is not None
    assert "could not fetch" in capsys.readouterr().err
