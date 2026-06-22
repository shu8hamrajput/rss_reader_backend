from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Category, Feed, User
from ..schemas import (
    CategoryResponse,
    FeedCreate,
    FeedListResponse,
    FeedResponse,
    FeedSnoozeRequest,
    FeedUpdate,
    RefreshResult,
)
from ..services.feed_parser import refresh_feed
from ..services.plans import effective_plan, limits_for

router = APIRouter(prefix="/feeds", tags=["Feeds"])


def _owned_feed(feed_id: int, user: User, db: Session) -> Feed:
    feed = (
        db.query(Feed)
        .filter(Feed.id == feed_id, Feed.user_id == user.id)
        .options(selectinload(Feed.categories))
        .first()
    )
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


def _batch_counts(feed_ids: list[int], db: Session) -> dict[int, tuple[int, int]]:
    """Single SQL query → {feed_id: (article_count, unread_count)}.

    Replaces the previous 2-queries-per-feed N+1 pattern in _build_feed_response.
    On a 25-feed account this cuts GET /feeds from ~75 queries down to 3.
    """
    if not feed_ids:
        return {}
    rows = db.execute(
        text("""
            SELECT feed_id,
                   COUNT(*)                              AS total,
                   COUNT(*) FILTER (WHERE is_read = false) AS unread
            FROM   articles
            WHERE  feed_id = ANY(:ids)
            GROUP  BY feed_id
        """),
        {"ids": feed_ids},
    ).fetchall()
    return {r.feed_id: (int(r.total), int(r.unread)) for r in rows}


def _to_feed_response(feed: Feed, article_count: int, unread_count: int) -> FeedResponse:
    data = FeedResponse.model_validate(feed)
    data.article_count = article_count
    data.unread_count = unread_count
    data.categories = [
        CategoryResponse(id=c.id, name=c.name, created_at=c.created_at)
        for c in feed.categories
    ]
    return data


def _build_feed_response(feed: Feed, db: Session) -> FeedResponse:
    """Single-feed path (create / update / snooze / get). Uses batch helper for consistency."""
    counts = _batch_counts([feed.id], db)
    article_count, unread_count = counts.get(feed.id, (0, 0))
    return _to_feed_response(feed, article_count, unread_count)


def _build_feed_list(feeds: list[Feed], db: Session) -> list[FeedResponse]:
    """Multi-feed path. Fetches all counts in one query instead of 2N queries."""
    counts = _batch_counts([f.id for f in feeds], db)
    return [
        _to_feed_response(f, *counts.get(f.id, (0, 0)))
        for f in feeds
    ]


def _apply_categories(feed: Feed, category_ids: list[int], user: User, db: Session) -> None:
    cats = db.query(Category).filter(
        Category.id.in_(category_ids), Category.user_id == user.id
    ).all()
    if len(cats) != len(category_ids):
        raise HTTPException(status_code=404, detail="One or more categories not found")
    feed.categories = cats


@router.post("", response_model=FeedResponse, status_code=status.HTTP_201_CREATED, summary="Subscribe to a feed")
async def create_feed(
    payload: FeedCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.query(Feed).filter(Feed.url == payload.url, Feed.user_id == current_user.id).first():
        raise HTTPException(status_code=409, detail="You are already subscribed to this feed")

    max_feeds = limits_for(effective_plan(current_user)).max_feeds
    if max_feeds is not None:
        feed_count = db.query(func.count(Feed.id)).filter(Feed.user_id == current_user.id).scalar() or 0
        if feed_count >= max_feeds:
            raise HTTPException(
                status_code=403,
                detail=f"Your plan allows up to {max_feeds} feeds. Upgrade to subscribe to more.",
            )

    feed = Feed(url=payload.url, title=payload.title, user_id=current_user.id)
    db.add(feed)
    db.flush()

    if payload.category_ids:
        _apply_categories(feed, payload.category_ids, current_user, db)

    db.commit()
    db.refresh(feed)

    try:
        await refresh_feed(feed, db)
        db.refresh(feed)
    except Exception:
        pass

    return _build_feed_response(feed, db)


@router.get("/health", response_model=FeedListResponse, summary="List feeds sorted by health (worst first)")
def list_feeds_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feeds = (
        db.query(Feed)
        .filter(Feed.user_id == current_user.id)
        .options(selectinload(Feed.categories))
        .order_by(Feed.fetch_failure_count.desc(), Feed.last_success_at.asc().nullsfirst())
        .all()
    )
    return FeedListResponse(total=len(feeds), items=_build_feed_list(feeds, db))


@router.get("", response_model=FeedListResponse, summary="List your subscribed feeds")
def list_feeds(
    active_only: bool = Query(False),
    category_id: int | None = Query(None, description="Filter by category ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = (
        db.query(Feed)
        .filter(Feed.user_id == current_user.id)
        .options(selectinload(Feed.categories))  # batch load categories in 1 query
    )
    if active_only:
        q = q.filter(Feed.is_active == True)
    if category_id is not None:
        q = q.filter(Feed.categories.any(id=category_id))
    feeds = q.order_by(Feed.created_at.desc()).all()
    return FeedListResponse(total=len(feeds), items=_build_feed_list(feeds, db))


@router.get("/{feed_id}", response_model=FeedResponse, summary="Get a feed by ID")
def get_feed(
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _build_feed_response(_owned_feed(feed_id, current_user, db), db)


@router.patch("/{feed_id}", response_model=FeedResponse, summary="Update a feed")
def update_feed(
    feed_id: int,
    payload: FeedUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feed = _owned_feed(feed_id, current_user, db)
    if payload.title is not None:
        feed.title = payload.title
    if payload.is_active is not None:
        feed.is_active = payload.is_active
    if payload.category_ids is not None:
        _apply_categories(feed, payload.category_ids, current_user, db)
    db.commit()
    db.refresh(feed)
    return _build_feed_response(feed, db)


@router.post("/{feed_id}/snooze", response_model=FeedResponse, summary="Snooze health warnings for a feed")
def snooze_feed(
    feed_id: int,
    payload: FeedSnoozeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime, timedelta, timezone
    feed = _owned_feed(feed_id, current_user, db)
    feed.health_snooze_until = datetime.now(timezone.utc) + timedelta(days=max(1, min(payload.days, 365)))
    feed.fetch_failure_count = 0
    db.commit()
    db.refresh(feed)
    return _build_feed_response(feed, db)


@router.delete("/{feed_id}/snooze", response_model=FeedResponse, summary="Cancel health snooze for a feed")
def unsnooze_feed(
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feed = _owned_feed(feed_id, current_user, db)
    feed.health_snooze_until = None
    db.commit()
    db.refresh(feed)
    return _build_feed_response(feed, db)


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Unsubscribe from a feed")
def delete_feed(
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.delete(_owned_feed(feed_id, current_user, db))
    db.commit()


@router.post("/{feed_id}/refresh", response_model=RefreshResult, summary="Fetch the latest articles")
async def refresh_feed_endpoint(
    request: Request,
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feed = _owned_feed(feed_id, current_user, db)
    try:
        new_count = await refresh_feed(feed, db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {exc}")
    return RefreshResult(feed_id=feed_id, new_articles=new_count, message=f"Fetched {new_count} new article(s)")
