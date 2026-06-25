from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session, subqueryload

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Category, Feed, User, feed_categories
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
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == user.id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


def _build_feed_response(feed: Feed, db: Session) -> FeedResponse:
    article_count = db.query(func.count(Article.id)).filter(Article.feed_id == feed.id).scalar() or 0
    unread_count = (
        db.query(func.count(Article.id))
        .filter(Article.feed_id == feed.id, Article.is_read == False)  # noqa: E712
        .scalar() or 0
    )
    data = FeedResponse.model_validate(feed)
    data.article_count = article_count
    data.unread_count = unread_count
    data.categories = [
        CategoryResponse(id=c.id, name=c.name, created_at=c.created_at, feed_count=len(c.feeds))
        for c in feed.categories
    ]
    return data


def _build_feed_responses_bulk(feeds: list[Feed], db: Session) -> list[FeedResponse]:
    """Build FeedResponse objects for a list of feeds using batched SQL — avoids N+1.

    Replaces per-feed COUNT queries in list endpoints with two aggregate queries
    (total articles, unread articles) plus one query for category feed-counts.
    Categories must already be eager-loaded on each Feed (e.g. via subqueryload).
    """
    if not feeds:
        return []
    feed_ids = [f.id for f in feeds]

    total_rows = (
        db.query(Article.feed_id, func.count(Article.id).label("cnt"))
        .filter(Article.feed_id.in_(feed_ids))
        .group_by(Article.feed_id)
        .all()
    )
    article_counts = {r.feed_id: r.cnt for r in total_rows}

    unread_rows = (
        db.query(Article.feed_id, func.count(Article.id).label("cnt"))
        .filter(Article.feed_id.in_(feed_ids), Article.is_read == False)  # noqa: E712
        .group_by(Article.feed_id)
        .all()
    )
    unread_counts = {r.feed_id: r.cnt for r in unread_rows}

    # Batch category feed-counts to avoid per-category lazy load of category.feeds
    cat_ids = list({c.id for f in feeds for c in f.categories})
    cat_feed_counts: dict[int, int] = {}
    if cat_ids:
        cat_rows = (
            db.query(
                feed_categories.c.category_id,
                func.count(feed_categories.c.feed_id).label("cnt"),
            )
            .filter(feed_categories.c.category_id.in_(cat_ids))
            .group_by(feed_categories.c.category_id)
            .all()
        )
        cat_feed_counts = {r[0]: r[1] for r in cat_rows}

    results = []
    for feed in feeds:
        data = FeedResponse.model_validate(feed)
        data.article_count = article_counts.get(feed.id, 0)
        data.unread_count = unread_counts.get(feed.id, 0)
        data.categories = [
            CategoryResponse(
                id=c.id,
                name=c.name,
                created_at=c.created_at,
                feed_count=cat_feed_counts.get(c.id, 0),
            )
            for c in feed.categories
        ]
        results.append(data)
    return results


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
        .options(subqueryload(Feed.categories))
        .order_by(Feed.fetch_failure_count.desc(), Feed.last_success_at.asc().nullsfirst())
        .all()
    )
    return FeedListResponse(total=len(feeds), items=_build_feed_responses_bulk(feeds, db))


@router.get("", response_model=FeedListResponse, summary="List your subscribed feeds")
def list_feeds(
    active_only: bool = Query(False),
    category_id: int | None = Query(None, description="Filter by category ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Feed).filter(Feed.user_id == current_user.id)
    if active_only:
        q = q.filter(Feed.is_active == True)  # noqa: E712
    if category_id is not None:
        q = q.filter(Feed.categories.any(id=category_id))
    feeds = q.options(subqueryload(Feed.categories)).order_by(Feed.created_at.desc()).all()
    return FeedListResponse(total=len(feeds), items=_build_feed_responses_bulk(feeds, db))


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
