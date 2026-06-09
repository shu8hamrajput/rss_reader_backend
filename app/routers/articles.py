import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Feed, User
from ..schemas import (
    ArticleBookmarkUpdate,
    ArticleListResponse,
    ArticleReadUpdate,
    ArticleResponse,
    ArticleResumeUpdate,
    ArticleScrollUpdate,
    ArticleTagsUpdate,
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


class _ArticleNotePayload(_BaseModel):
    note: str | None = None

router = APIRouter(prefix="/articles", tags=["Articles"])


def _owned_article(article_id: int, user: User, db: Session) -> Article:
    article = (
        db.query(Article)
        .join(Feed)
        .filter(Article.id == article_id, Feed.user_id == user.id)
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


def _fts_ids(search: str, db: Session) -> list[int] | None:
    """Return article IDs matching the Postgres full-text query, ranked by relevance,
    or None if the search_vector index is unavailable (fall back to ILIKE)."""
    try:
        rows = db.execute(
            text(
                """SELECT id FROM articles
                   WHERE search_vector @@ plainto_tsquery('english', :q)
                   ORDER BY ts_rank(search_vector, plainto_tsquery('english', :q)) DESC"""
            ),
            {"q": search},
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
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
        q = q.filter(Article.media_type.like('audio/%'))

    if search:
        fts_article_ids = _fts_ids(search, db)
        if fts_article_ids is not None:
            if not fts_article_ids:
                return ArticleListResponse(total=0, page=page, page_size=page_size, items=[])
            q = q.filter(Article.id.in_(fts_article_ids))
        else:
            term = f"%{search}%"
            q = q.filter(or_(Article.title.ilike(term), Article.summary.ilike(term)))

    total = q.count()
    items = (
        q.order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ArticleListResponse(
        total=total, page=page, page_size=page_size,
        items=[ArticleResponse.model_validate(a) for a in items],
    )


@router.get("/stats", response_model=ReadingStatsResponse, summary="Reading activity statistics")
def get_reading_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    base = db.query(Article).join(Feed).filter(Feed.user_id == current_user.id)

    total_articles = base.count()
    total_read = base.filter(Article.is_read == True).count()
    total_bookmarked = base.filter(Article.is_bookmarked == True).count()

    now = datetime.now(timezone.utc)
    today = now.date()
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    week_start = today_start - timedelta(days=6)

    read_today = base.filter(Article.read_at >= today_start).count()
    read_this_week = base.filter(Article.read_at >= week_start).count()

    # Daily read counts for the last 30 days
    window_start = today_start - timedelta(days=29)
    daily_rows = (
        base.filter(Article.read_at >= window_start)
        .with_entities(func.date(Article.read_at).label("d"), func.count(Article.id))
        .group_by("d")
        .all()
    )
    counts_by_date = {row[0].isoformat(): row[1] for row in daily_rows}
    daily_counts = [
        DailyReadCount(date=d.isoformat(), count=counts_by_date.get(d.isoformat(), 0))
        for d in (window_start.date() + timedelta(days=i) for i in range(30))
    ]

    # Streaks: derived from the distinct set of days with at least one read article
    read_dates = sorted(
        {row[0] for row in base.filter(Article.read_at.isnot(None))
            .with_entities(func.date(Article.read_at)).distinct().all()}
    )
    current_streak = 0
    longest_streak = 0
    if read_dates:
        run = 1
        longest_streak = 1
        for prev, curr in zip(read_dates, read_dates[1:]):
            if curr - prev == timedelta(days=1):
                run += 1
            else:
                run = 1
            longest_streak = max(longest_streak, run)

        last_date = read_dates[-1]
        if last_date in (today, today - timedelta(days=1)):
            current_streak = 1
            for i in range(len(read_dates) - 1, 0, -1):
                if read_dates[i] - read_dates[i - 1] == timedelta(days=1):
                    current_streak += 1
                else:
                    break

    top_feed_rows = (
        base.filter(Article.is_read == True)
        .with_entities(Feed.id, Feed.title, func.count(Article.id).label("c"))
        .group_by(Feed.id, Feed.title)
        .order_by(func.count(Article.id).desc())
        .limit(5)
        .all()
    )
    top_feeds = [TopFeedStat(feed_id=row[0], title=row[1], read_count=row[2]) for row in top_feed_rows]

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
    return (
        db.query(Article)
        .join(Feed)
        .filter(Article.id.in_(article_ids), Feed.user_id == user.id)
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


# System-managed tags that users should not overwrite via the tags endpoint
_SYSTEM_TAGS = frozenset({"saved_later", "read_later", "read", "unread"})


@router.get("/user-tags", response_model=UserTagsResponse, summary="List all distinct user tags")
def get_user_tags(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the union of all non-system tags the user has applied across their articles."""
    rows = (
        db.query(Article.tags)
        .join(Feed)
        .filter(Feed.user_id == current_user.id, Article.tags.isnot(None))
        .all()
    )
    seen: set[str] = set()
    for (raw,) in rows:
        try:
            for t in json.loads(raw or "[]"):
                if t not in _SYSTEM_TAGS:
                    seen.add(t)
        except (json.JSONDecodeError, TypeError):
            pass
    return UserTagsResponse(tags=sorted(seen))


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
