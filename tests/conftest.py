import itertools
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

# ── Point the app at a dedicated test database, before any app.* import ───────
# app/config.py reads DATABASE_URL at import time and app/database.py builds its
# engine from settings.database_url at import time, so every later app.* import
# (including app.main's routers) ends up bound to the test database.

_DEFAULT_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/rss_reader"
_base_url = os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)
_base_prefix, _db_name = _base_url.rsplit("/", 1)
_test_db_name = f"{_db_name}_test"
os.environ["DATABASE_URL"] = f"{_base_prefix}/{_test_db_name}"


def _ensure_database_exists() -> None:
    admin_url = _base_prefix.replace("postgresql+psycopg://", "postgresql://") + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_test_db_name,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_test_db_name}"')


_ensure_database_exists()

from app import database as db_module  # noqa: E402
from app.auth import create_access_token  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import _migrate, app  # noqa: E402
from app.models import Article, Category, Collection, Feed, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_schema():
    Base.metadata.create_all(bind=db_module.engine)
    _migrate()
    yield


@pytest.fixture()
def db_session():
    connection = db_module.engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session):
    u = User(google_id="google-test-user-1", email="user1@example.com", name="Test User")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def other_user(db_session):
    u = User(google_id="google-test-user-2", email="user2@example.com", name="Other User")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def auth_headers(user):
    return auth_headers_for(user)


@pytest.fixture()
def other_auth_headers(other_user):
    return auth_headers_for(other_user)


def auth_headers_for(user: User) -> dict:
    token, _ = create_access_token(user.id, user.email, user.token_version)
    return {"Authorization": f"Bearer {token}"}


# ── Fixture data helpers ────────────────────────────────────────────────────

_feed_seq = itertools.count(1)
_article_seq = itertools.count(1)
_category_seq = itertools.count(1)
_collection_seq = itertools.count(1)


def make_feed(db_session, user, **kw) -> Feed:
    n = next(_feed_seq)
    defaults = dict(
        url=f"https://example.com/feed-{n}.xml",
        title=f"Feed {n}",
        user_id=user.id,
    )
    defaults.update(kw)
    feed = Feed(**defaults)
    db_session.add(feed)
    db_session.commit()
    db_session.refresh(feed)
    return feed


def make_article(db_session, feed, **kw) -> Article:
    n = next(_article_seq)
    defaults = dict(
        feed_id=feed.id,
        guid=f"guid-{n}",
        title=f"Article {n}",
        url=f"https://example.com/article-{n}",
        summary=f"Summary {n}",
        content=f"Content {n}",
    )
    defaults.update(kw)
    article = Article(**defaults)
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)
    return article


def make_category(db_session, user, **kw) -> Category:
    n = next(_category_seq)
    defaults = dict(
        user_id=user.id,
        name=f"Category {n}",
    )
    defaults.update(kw)
    category = Category(**defaults)
    db_session.add(category)
    db_session.commit()
    db_session.refresh(category)
    return category


def make_collection(db_session, owner, **kw) -> Collection:
    n = next(_collection_seq)
    defaults = dict(
        owner_id=owner.id,
        name=f"Collection {n}",
        slug=f"collection-{n}",
    )
    defaults.update(kw)
    collection = Collection(**defaults)
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    return collection
