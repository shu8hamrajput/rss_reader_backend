import pytest

from app.config import settings
from app.models import GeneratedCandidate
from app.services.fetchers import _registry
from app.services.parser_gen import codegen, samples
from app.tasks import _generate_candidate

from ..conftest import auth_headers_for, make_feed

_ARTICLE_HTML = "<html><body><article>" + ("Lorem ipsum dolor sit amet. " * 20) + "</article></body></html>"
_ARTICLE_URL = "https://example.com/articles/foo"


@pytest.fixture()
def fetchers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(codegen, "_FETCHERS_DIR", tmp_path)
    (tmp_path / "generated" / "candidates").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def admin_headers(user, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", user.email)
    return auth_headers_for(user)


@pytest.fixture(autouse=True)
def _sample_urls(monkeypatch):
    monkeypatch.setattr(samples, "sample_article_urls", lambda url, n: ([_ARTICLE_URL], True))
    monkeypatch.setattr(samples, "fetch_html", lambda url: _ARTICLE_HTML)


def _make_ready_candidate(db_session, feed) -> GeneratedCandidate:
    candidate = GeneratedCandidate(feed_id=feed.id, domain="example.com", slug="example_com", status="pending")
    db_session.add(candidate)
    db_session.commit()
    db_session.refresh(candidate)
    _generate_candidate(db_session, candidate, feed.url, use_llm=False)
    db_session.refresh(candidate)
    return candidate


def test_generate_fetcher_requires_admin(client, db_session, user, auth_headers):
    feed = make_feed(db_session, user)
    resp = client.post(f"/api/v1/feeds/{feed.id}/generate-fetcher", json={}, headers=auth_headers)
    assert resp.status_code == 403


def test_generate_fetcher_404_for_other_users_feed(client, db_session, other_user, admin_headers):
    feed = make_feed(db_session, other_user)
    resp = client.post(f"/api/v1/feeds/{feed.id}/generate-fetcher", json={}, headers=admin_headers)
    assert resp.status_code == 404


def test_generate_fetcher_creates_pending_candidate(client, db_session, user, admin_headers):
    feed = make_feed(db_session, user)
    resp = client.post(f"/api/v1/feeds/{feed.id}/generate-fetcher", json={}, headers=admin_headers)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["domain"] == "example.com"
    assert body["slug"] == "example_com"


def test_generate_candidate_writes_ready_candidate(fetchers_dir, db_session, user):
    feed = make_feed(db_session, user)
    candidate = _make_ready_candidate(db_session, feed)

    assert candidate.status == "ready"
    assert candidate.mode == "heuristic"
    candidate_path = fetchers_dir / "generated" / "candidates" / "example_com.py"
    assert candidate_path.exists()


def test_list_candidates_includes_detail_when_ready(fetchers_dir, client, db_session, user, admin_headers):
    feed = make_feed(db_session, user)
    _make_ready_candidate(db_session, feed)

    resp = client.get(f"/api/v1/feeds/{feed.id}/candidates", headers=admin_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "ready"

    detail = rows[0]["candidate"]
    assert detail["domain"] == "example.com"
    assert detail["content_selectors"] == ["article"]
    assert detail["before_chars"][_ARTICLE_URL] == 0
    assert detail["after_chars"][_ARTICLE_URL] > 0


def test_approve_candidate_hot_loads_fetcher(fetchers_dir, client, db_session, user, admin_headers):
    feed = make_feed(db_session, user)
    feed.fetch_failure_count = 5
    db_session.commit()

    candidate = _make_ready_candidate(db_session, feed)

    resp = client.post(
        f"/api/v1/feeds/{feed.id}/candidates/{candidate.id}/approve", headers=admin_headers
    )
    try:
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"

        candidate_path = fetchers_dir / "generated" / "candidates" / "example_com.py"
        active_path = fetchers_dir / "generated" / "example_com.py"
        assert not candidate_path.exists()
        assert active_path.exists()

        fetcher = _registry._resolve(_ARTICLE_URL)
        assert fetcher.__module__ == "example_com"

        db_session.refresh(feed)
        assert feed.fetch_failure_count == 0

        # Re-approving a non-"ready" candidate is rejected
        resp2 = client.post(
            f"/api/v1/feeds/{feed.id}/candidates/{candidate.id}/approve", headers=admin_headers
        )
        assert resp2.status_code == 409
    finally:
        _registry.unregister(r"example\.com/")


def test_approve_unknown_candidate_404(fetchers_dir, client, db_session, user, admin_headers):
    feed = make_feed(db_session, user)
    resp = client.post(f"/api/v1/feeds/{feed.id}/candidates/999999/approve", headers=admin_headers)
    assert resp.status_code == 404
