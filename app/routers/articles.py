import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Feed, User
from ..schemas import (
    ArticleBookmarkUpdate,
    ArticleListResponse,
    ArticleReadUpdate,
    ArticleResponse,
    BulkActionResponse,
    BulkBookmarkRequest,
    BulkMarkReadRequest,
    BulkSaveLaterResponse,
    BulkTagRequest,
)
from ..services.article_fetcher import fetch_full_content

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
    """Return article IDs matching the FTS5 query, or None if FTS is unavailable."""
    try:
        safe = search.replace('"', '""')
        rows = db.execute(
            text('SELECT rowid FROM articles_fts WHERE articles_fts MATCH :q ORDER BY rank'),
            {"q": f'"{safe}"'},
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return None  # FTS table not yet created — fall back to ILIKE


@router.get("", response_model=ArticleListResponse, summary="List articles with filtering and pagination")
def list_articles(
    feed_id: int | None = Query(None, description="Filter by feed ID"),
    category_id: int | None = Query(None, description="Filter by category ID"),
    is_read: bool | None = Query(None, description="Filter by read status"),
    is_bookmarked: bool | None = Query(None, description="Filter by bookmark status"),
    tag: str | None = Query(None, description="Filter by tag (e.g. read_later, saved_later)"),
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
        # JSON array contains the tag — SQLite JSON_EACH approach via LIKE for portability
        q = q.filter(Article.tags.like(f'%"{tag}"%'))

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


@router.get("/{article_id}", response_model=ArticleResponse, summary="Get a single article")
def get_article(
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ArticleResponse.model_validate(_owned_article(article_id, current_user, db))


@router.patch("/{article_id}/read", response_model=ArticleResponse, summary="Mark read / unread")
def update_read_status(
    article_id: int,
    payload: ArticleReadUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    article = _owned_article(article_id, current_user, db)
    article.is_read = payload.is_read
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
        {"is_read": True}
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
        if urls:
            results = await asyncio.gather(*[fetch_full_content(u) for u in urls])
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
        if payload.is_read:
            _add_tag(article, "read")
            _remove_tag(article, "unread")
        else:
            _add_tag(article, "unread")
            _remove_tag(article, "read")
    db.commit()
    return BulkActionResponse(updated=len(articles))
