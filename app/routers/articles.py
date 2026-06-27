import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, defer
from sqlalchemy.orm.attributes import set_committed_value

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Feed, ParserRequest, User
from ..schemas import (
    ArticleBookmarkUpdate,
    ArticleListResponse,
    ArticleReadUpdate,
    ArticleResponse,
    ArticleResumeUpdate,
    ArticleScrollUpdate,
    ArticleTagsUpdate,
    ParserRequestCreate,
    ParserRequestResponse,
    UserTagsResponse,
    BulkActionResponse,
    BulkBookmarkRequest,
    BulkMarkReadRequest,
    BulkSaveLaterResponse,
    BulkTagRequest,
    DailyReadCount,
    ReadingStatsResponse,
    TopFeedStat,
)
from pydantic import BaseModel as _BaseModel
from ..services.article_fetcher import fetch_full_content
from ..services.usage import record_fetches, remaining_fetch_quota

logger = logging.getLogger(__name__)


class _ArticleNotePayload(_BaseModel):
    note: str | None = None

router = APIRouter(prefix="/articles", tags=["Articles"])


def _owned_article(article_id: int, user: User, db: Session) -> Article:
    article = (
        db.query(Article)
        .join(Feed)
        .filter(Article.id == article_id, Feed.user_id == user.id)
        .options(defer(Article.search_vector))   # binary tsvector never serialised
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


_FTS_ID_LIMIT = 500  # max article IDs pulled into Python for an IN() filter

def _fts_ids(search: str, db: Session) -> list[int] | None:
    """Return up to _FTS_ID_LIMIT article IDs matching the Postgres FTS query, ranked by
    relevance. Returns None when the search_vector index is unavailable so the caller
    can fall back to ILIKE. Capping at 500 prevents loading thousands of IDs into the
    Python heap for broad search terms."""
    try:
        rows = db.execute(
            text(
                """SELECT id FROM articles
                   WHERE search_vector @@ plainto_tsquery('english', :q)
                   ORDER BY ts_rank(search_vector, plainto_tsquery('english', :q)) DESC
                   LIMIT :lim"""
            ),
            {"q": search, "lim": _FTS_ID_LIMIT},
        ).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.warning("FTS search failed, falling back to ILIKE: %s", exc)
        db.rollback()  # Postgres aborts the transaction on error — must roll back before reuse
        return None  # search_vector not yet populated — fall back to ILIKE


@router.get("", response_model=ArticleListResponse, summary="List articles with filtering and pagination")
def list_articles(
    feed_id: int | None = Query(None, description="Filter by feed ID"),
    category_id: int | None = Query(None, description="Filter by category ID"),
    is_read: bool | None = Query(None, description="Filter by read status"),
    is_bookmarked: bool | None = Query(None, description="Filter by bookmark status"),
    tag: str | None = Query(None, description="Filter by tag (e.g. read_later, saved_later)"),
    has_audio: bool | None = Query(None, description="Filter to audio/podcast episodes only"),
    in_progress: bool | None = Query(None, description="Filter to episodes with a saved resume position"),
    search: str | None = Query(None, description="Full-text search (title + summary + content)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Article).join(Feed).filter(Feed.user_id == current_user.id)

    if feed_id is not None:
        feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == current_user.id).first()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")
        q = q.filter(Article.feed_id == feed_id)

    if category_id is not None:
        q = q.filter(Feed.categories.any(id=category_id))

    if is_read is not None:
        q = q.filter(Article.is_read == is_read)

    if is_bookmarked is not None:
        q = q.filter(Article.is_bookmarked == is_bookmarked)

    if tag is not None:
        # JSON array contains the tag — simple LIKE match keeps this portable across SQLite/Postgres
        q = q.filter(Article.tags.like(f'%"{tag}"%'))

    if has_audio:
        q = q.filter(or_(Article.media_type.like('audio/%'), Article.media_type == 'video/youtube'))

    if in_progress:
        q = q.filter(Article.resume_at_seconds.isnot(None))

    if search:
        fts_article_ids = _fts_ids(search, db)
        if fts_article_ids is not None:
            if not fts_article_ids:
                return ArticleListResponse(total=0, page=page, page_size=page_size, items=[])
            q = q.filter(Article.id.in_(fts_article_ids))
        else:
            term = f"%{search}%"
            q = q.filter(or_(Article.title.ilike(term), Article.summary.ilike(term)))

    # Use a window function to get the total count and the page rows in ONE query
    # instead of two separate round-trips (q.count() + q.all()).
    # full_content / search_vector are deferred: full_content is MB of fetched HTML
    # irrelevant for list/card rendering; search_vector is a binary tsvector that is
    # never serialised. The reader falls back to `content` (the RSS body) when
    # full_content is None; users can trigger a refetch from the reader toolbar.
    count_col = func.count().over().label("_total")
    q_paged = (
        q
        .options(
            defer(Article.content),        # RSS HTML (50–200 KB/article) — reader fetches GET /articles/{id}
            defer(Article.full_content),   # fetched HTML (up to MBs) — reader fetches on demand
            defer(Article.search_vector),  # binary tsvector — never serialised
        )
        .order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
        .add_columns(count_col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = q_paged.all()
    total = rows[0]._total if rows else 0
    items = []
    for row in rows:
        # Pre-fill deferred large-text columns so Pydantic validation doesn't
        # trigger a per-row lazy SELECT (N+1). List cards don't need these fields;
        # the reader fetches them via GET /articles/{id}.
        set_committed_value(row.Article, 'content', None)
        set_committed_value(row.Article, 'full_content', None)
        items.append(ArticleResponse.model_validate(row.Article))

    return ArticleListResponse(
        total=total, page=page, page_size=page_size, items=items,
    )


@router.get("/stats", response_model=ReadingStatsResponse, summary="Reading activity statistics")
def get_reading_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    today = now.date()
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    week_start  = today_start - timedelta(days=6)
    window_start = today_start - timedelta(days=29)

    # Single query for all aggregate counters — replaces 5 separate COUNT round-trips.
    agg = db.execute(
        text("""
            SELECT
              COUNT(*)                                                   AS total_articles,
              COUNT(*) FILTER (WHERE a.is_read = true)                  AS total_read,
              COUNT(*) FILTER (WHERE a.is_bookmarked = true)            AS total_bookmarked,
              COUNT(*) FILTER (WHERE a.read_at >= :today_start)         AS read_today,
              COUNT(*) FILTER (WHERE a.read_at >= :week_start)          AS read_this_week
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            WHERE f.user_id = :uid
        """),
        {"uid": current_user.id, "today_start": today_start, "week_start": week_start},
    ).fetchone()

    total_articles  = int(agg.total_articles)
    total_read      = int(agg.total_read)
    total_bookmarked = int(agg.total_bookmarked)
    read_today      = int(agg.read_today)
    read_this_week  = int(agg.read_this_week)

    # Daily read counts for the past 30 days — single GROUP BY query.
    daily_rows = db.execute(
        text("""
            SELECT DATE(a.read_at AT TIME ZONE 'UTC') AS d, COUNT(*) AS n
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            WHERE f.user_id = :uid AND a.read_at >= :window_start
            GROUP BY d
        """),
        {"uid": current_user.id, "window_start": window_start},
    ).fetchall()
    counts_by_date: dict[str, int] = {str(r.d): int(r.n) for r in daily_rows}
    daily_counts = [
        DailyReadCount(
            date=(window_start.date() + timedelta(days=i)).isoformat(),
            count=counts_by_date.get((window_start.date() + timedelta(days=i)).isoformat(), 0),
        )
        for i in range(30)
    ]

    # Distinct read-dates for streak calculation — fetch only the date column.
    streak_rows = db.execute(
        text("""
            SELECT DISTINCT DATE(a.read_at AT TIME ZONE 'UTC') AS d
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            WHERE f.user_id = :uid AND a.read_at IS NOT NULL
            ORDER BY d
        """),
        {"uid": current_user.id},
    ).fetchall()
    read_dates = [r.d for r in streak_rows]  # list[datetime.date], already sorted

    current_streak = 0
    longest_streak = 0
    if read_dates:
        run = longest_streak = 1
        for prev, curr in zip(read_dates, read_dates[1:]):
            run = run + 1 if (curr - prev).days == 1 else 1
            longest_streak = max(longest_streak, run)
        last = read_dates[-1]
        if last in (today, today - timedelta(days=1)):
            current_streak = 1
            for i in range(len(read_dates) - 1, 0, -1):
                if (read_dates[i] - read_dates[i - 1]).days == 1:
                    current_streak += 1
                else:
                    break

    # Top 5 feeds by read count — one GROUP BY query.
    top_feed_rows = db.execute(
        text("""
            SELECT f.id, f.title, COUNT(*) AS c
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            WHERE f.user_id = :uid AND a.is_read = true
            GROUP BY f.id, f.title
            ORDER BY c DESC
            LIMIT 5
        """),
        {"uid": current_user.id},
    ).fetchall()
    top_feeds = [TopFeedStat(feed_id=r.id, title=r.title, read_count=int(r.c)) for r in top_feed_rows]

    return ReadingStatsResponse(
        total_articles=total_articles,
        total_read=total_read,
        total_unread=total_articles - total_read,
        total_bookmarked=total_bookmarked,
        read_today=read_today,
        read_this_week=read_this_week,
        current_streak=current_streak,
        longest_streak=longest_streak,
        daily_counts=daily_counts,
        top_feeds=top_feeds,
    )


# System-managed tags that users should not overwrite via the tags endpoint
_SYSTEM_TAGS = frozenset({"saved_later", "read_later", "read", "unread"})


@router.get("/user-tags", response_model=UserTagsResponse, summary="List all distinct user tags")
def get_user_tags(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the union of all non-system tags the user has applied across their articles.

    Uses jsonb_array_elements_text to flatten and deduplicate tags entirely in SQL,
    avoiding loading every article's tag JSON into Python for client-side union.
    """
    system_tags = list(_SYSTEM_TAGS)
    rows = db.execute(
        text("""
            SELECT DISTINCT tag
            FROM   articles a
            JOIN   feeds f ON a.feed_id = f.id,
                   jsonb_array_elements_text(a.tags::jsonb) AS tag
            WHERE  f.user_id = :uid
              AND  a.tags IS NOT NULL
              AND  a.tags != 'null'
              AND  tag != ALL(:system_tags)
            ORDER  BY tag
        """),
        {"uid": current_user.id, "system_tags": system_tags},
    ).fetchall()
    return UserTagsResponse(tags=[r.tag for r in rows])


@router.get("/{article_id}", response_model=ArticleResponse, summary="Get a single article")
def get_article(
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ArticleResponse.model_validate(_owned_article(article_id, current_user, db))


@router.post("/{article_id}/refetch", response_model=ArticleResponse, summary="Re-fetch and store full article content")
async def refetch_article_content(
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    if not article.url:
        raise HTTPException(status_code=422, detail="Article has no URL to fetch from")

    remaining = remaining_fetch_quota(current_user)
    if remaining is not None and remaining <= 0:
        raise HTTPException(
            status_code=429,
            detail="Daily full-content fetch limit reached for your plan. Try again tomorrow or upgrade.",
        )

    html = await fetch_full_content(article.url)
    record_fetches(current_user, 1)
    if html:
        article.full_content = html
        db.commit()
        db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.post(
    "/{article_id}/request-parser",
    response_model=ParserRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a better content extractor for this article's domain",
)
def request_parser(
    article_id: int,
    payload: ParserRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    if not article.url:
        raise HTTPException(status_code=422, detail="Article has no URL")

    domain = urlparse(article.url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain:
        raise HTTPException(status_code=422, detail="Could not determine domain from article URL")

    existing = db.query(ParserRequest).filter(
        ParserRequest.user_id == current_user.id,
        ParserRequest.domain == domain,
        ParserRequest.status == "pending",
    ).first()
    if existing:
        return existing

    req = ParserRequest(
        user_id=current_user.id,
        article_id=article.id,
        url=article.url,
        domain=domain,
        note=payload.note,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@router.patch("/{article_id}/read", response_model=ArticleResponse, summary="Mark read / unread")
def update_read_status(
    article_id: int,
    payload: ArticleReadUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.is_read = payload.is_read
    article.read_at = datetime.now(timezone.utc) if payload.is_read else None
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.patch("/{article_id}/bookmark", response_model=ArticleResponse, summary="Bookmark / un-bookmark")
def update_bookmark_status(
    article_id: int,
    payload: ArticleBookmarkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.is_bookmarked = payload.is_bookmarked
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.post(
    "/feeds/{feed_id}/mark-all-read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark all articles in a feed as read",
    tags=["Articles"],
)
def mark_all_read(
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == current_user.id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.query(Article).filter(Article.feed_id == feed_id, Article.is_read == False).update(
        {"is_read": True, "read_at": datetime.now(timezone.utc)}
    )
    db.commit()


# ── Tag helpers ───────────────────────────────────────────────────────────────

def _get_tags(article: Article) -> list[str]:
    if not article.tags:
        return []
    try:
        return json.loads(article.tags)
    except (json.JSONDecodeError, TypeError):
        return []


def _set_tags(article: Article, tags: list[str]) -> None:
    article.tags = json.dumps(sorted(set(tags))) if tags else None


def _add_tag(article: Article, tag: str) -> None:
    tags = _get_tags(article)
    if tag not in tags:
        tags.append(tag)
    _set_tags(article, tags)


def _remove_tag(article: Article, tag: str) -> None:
    _set_tags(article, [t for t in _get_tags(article) if t != tag])


def _owned_articles(article_ids: list[int], user: User, db: Session) -> list[Article]:
    # Bulk mutation endpoints (mark-read, bookmark, tag) only touch metadata columns.
    # Defer the large content fields to avoid loading MB of HTML for no reason.
    return (
        db.query(Article)
        .join(Feed)
        .filter(Article.id.in_(article_ids), Feed.user_id == user.id)
        .options(
            defer(Article.full_content),
            defer(Article.content),
            defer(Article.search_vector),
        )
        .all()
    )


# ── Bulk endpoints ────────────────────────────────────────────────────────────

@router.post(
    "/bulk/read-later",
    response_model=BulkActionResponse,
    summary="Add or remove the 'read_later' tag on a set of articles",
)
def bulk_read_later(
    payload: BulkTagRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    articles = _owned_articles(payload.article_ids, current_user, db)
    for article in articles:
        if payload.value:
            _add_tag(article, "read_later")
        else:
            _remove_tag(article, "read_later")
    db.commit()
    return BulkActionResponse(updated=len(articles))


@router.post(
    "/bulk/bookmark",
    response_model=BulkActionResponse,
    summary="Bookmark or unbookmark a set of articles",
)
def bulk_bookmark(
    payload: BulkBookmarkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    articles = _owned_articles(payload.article_ids, current_user, db)
    for article in articles:
        article.is_bookmarked = payload.is_bookmarked
    db.commit()
    return BulkActionResponse(updated=len(articles))


@router.post(
    "/bulk/save-later",
    response_model=BulkSaveLaterResponse,
    summary="Add or remove the 'saved_later' tag; when adding, fetches and stores full article HTML",
)
async def bulk_save_later(
    payload: BulkTagRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    articles = _owned_articles(payload.article_ids, current_user, db)
    fetched = 0

    if payload.value:
        urls = [a.url for a in articles if a.url and not a.full_content]

        remaining = remaining_fetch_quota(current_user)
        if remaining is not None:
            urls = urls[:remaining]  # plan ran out — fetch as many as the quota allows

        if urls:
            results = await asyncio.gather(*[fetch_full_content(u) for u in urls])
            record_fetches(current_user, len(urls))
            url_to_content = {url: content for url, content in zip(urls, results)}
            for article in articles:
                if article.url and article.url in url_to_content:
                    html = url_to_content[article.url]
                    if html:
                        article.full_content = html
                        fetched += 1
        for article in articles:
            _add_tag(article, "saved_later")
    else:
        for article in articles:
            _remove_tag(article, "saved_later")

    db.commit()
    return BulkSaveLaterResponse(updated=len(articles), fetched=fetched)


@router.post(
    "/bulk/mark-read",
    response_model=BulkActionResponse,
    summary="Mark a set of articles as read or unread (also adds/removes 'read' tag)",
)
def bulk_mark_read(
    payload: BulkMarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    articles = _owned_articles(payload.article_ids, current_user, db)
    for article in articles:
        article.is_read = payload.is_read
        article.read_at = datetime.now(timezone.utc) if payload.is_read else None
        if payload.is_read:
            _add_tag(article, "read")
            _remove_tag(article, "unread")
        else:
            _add_tag(article, "unread")
            _remove_tag(article, "read")
    db.commit()
    return BulkActionResponse(updated=len(articles))


@router.patch("/{article_id}/tags", response_model=ArticleResponse, summary="Replace user-defined tags on an article")
def update_article_tags(
    article_id: int,
    payload: ArticleTagsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replaces the user-defined portion of the article's tag list, preserving any
    system tags (saved_later, read_later, read, unread) that are already present."""
    article = _owned_article(article_id, current_user, db)
    existing_system = [t for t in _get_tags(article) if t in _SYSTEM_TAGS]
    # Reject obviously invalid tag values (no whitespace, max length)
    new_user_tags = [t.strip() for t in payload.tags if t.strip() and len(t.strip()) <= 64]
    _set_tags(article, existing_system + new_user_tags)
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.patch("/{article_id}/note", response_model=ArticleResponse, summary="Save article-level note")
def update_article_note(
    article_id: int,
    payload: _ArticleNotePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.article_note = payload.note.strip() if payload.note else None
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.patch(
    "/{article_id}/resume",
    response_model=ArticleResponse,
    summary="Save playback resume position for a podcast episode",
)
def update_resume_position(
    article_id: int,
    payload: ArticleResumeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.resume_at_seconds = payload.resume_at_seconds
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)


@router.patch(
    "/{article_id}/scroll",
    response_model=ArticleResponse,
    summary="Save reading scroll position (0-100%) for cross-device resume",
)
def update_scroll_position(
    article_id: int,
    payload: ArticleScrollUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.scroll_pct = max(0, min(100, payload.scroll_pct))
    db.commit()
    db.refresh(article)
    return ArticleResponse.model_validate(article)
