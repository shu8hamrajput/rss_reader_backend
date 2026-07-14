import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session, selectinload, defer

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
from ..plugins import plugin_registry

router = APIRouter(prefix="/feeds", tags=["Feeds"])
logger = logging.getLogger(__name__)


@router.get("/plugins", tags=["Feeds"], summary="List available feed plugins")
def list_plugins():
    """Return metadata for all registered feed plugins."""
    return [
        {
            "name":         p.name,
            "display_name": p.display_name,
            "description":  p.description,
            "icon_emoji":   p.icon_emoji,
        }
        for p in plugin_registry.all_plugins
    ]


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


def _batch_counts(feed_ids: list[int], db: Session) -> dict[int, tuple[int, int, datetime | None]]:
    """Single SQL query → {feed_id: (article_count, unread_count, last_read_at)}.

    Replaces the previous 2-queries-per-feed N+1 pattern in _build_feed_response.
    On a 25-feed account this cuts GET /feeds from ~75 queries down to 3.
    last_read_at feeds the unsubscribe-suggestion heuristic below.
    """
    if not feed_ids:
        return {}
    rows = db.execute(
        text("""
            SELECT feed_id,
                   COUNT(*)                              AS total,
                   COUNT(*) FILTER (WHERE is_read = false) AS unread,
                   MAX(read_at)                           AS last_read_at
            FROM   articles
            WHERE  feed_id = ANY(:ids)
            GROUP  BY feed_id
        """),
        {"ids": feed_ids},
    ).fetchall()
    return {r.feed_id: (int(r.total), int(r.unread), r.last_read_at) for r in rows}


_UNSUBSCRIBE_SUGGESTION_DAYS = 30


def _suggest_unsubscribe(feed: Feed, article_count: int, last_read_at: datetime | None) -> bool:
    """Flag feeds that have accumulated articles nobody has read in a month.

    Requires the subscription itself to be at least as old as the threshold,
    so a feed added last week with unread articles isn't flagged prematurely.
    """
    if article_count == 0:
        return False
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_UNSUBSCRIBE_SUGGESTION_DAYS)
    if feed.created_at > cutoff:
        return False
    return last_read_at is None or last_read_at < cutoff


def _to_feed_response(feed: Feed, article_count: int, unread_count: int, last_read_at: datetime | None = None) -> FeedResponse:
    data = FeedResponse.model_validate(feed)
    data.article_count = article_count
    data.unread_count = unread_count
    data.suggest_unsubscribe = _suggest_unsubscribe(feed, article_count, last_read_at)
    data.categories = [
        CategoryResponse(id=c.id, name=c.name, created_at=c.created_at)
        for c in feed.categories
    ]
    return data


def _build_feed_response(feed: Feed, db: Session) -> FeedResponse:
    """Single-feed path (create / update / snooze / get). Uses batch helper for consistency."""
    counts = _batch_counts([feed.id], db)
    article_count, unread_count, last_read_at = counts.get(feed.id, (0, 0, None))
    return _to_feed_response(feed, article_count, unread_count, last_read_at)


def _build_feed_list(feeds: list[Feed], db: Session) -> list[FeedResponse]:
    """Multi-feed path. Fetches all counts in one query instead of 2N queries."""
    counts = _batch_counts([f.id for f in feeds], db)
    return [
        _to_feed_response(f, *counts.get(f.id, (0, 0, None)))
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
    feed_url = payload.url.strip()

    # Let the matching plugin normalize the URL (e.g. YouTube channel page → RSS URL).
    plugin = plugin_registry.get_plugin(feed_url)
    normalized = plugin.normalize_url(feed_url)
    if normalized != feed_url:
        feed_url = normalized
    elif plugin.name == "youtube" and not feed_url.startswith("https://www.youtube.com/feeds/"):
        # YouTube plugin matched but couldn't normalize — try the old resolver as fallback.
        from ..routers.search import _resolve_youtube_url
        rss = await _resolve_youtube_url(feed_url)
        if rss:
            feed_url = rss
        else:
            raise HTTPException(
                status_code=422,
                detail="Could not resolve YouTube channel ID. Paste the channel page URL (e.g. youtube.com/@handle).",
            )

    if db.query(Feed).filter(Feed.url == feed_url, Feed.user_id == current_user.id).first():
        raise HTTPException(status_code=409, detail="You are already subscribed to this feed")

    max_feeds = limits_for(effective_plan(current_user)).max_feeds
    if max_feeds is not None:
        feed_count = db.query(func.count(Feed.id)).filter(Feed.user_id == current_user.id).scalar() or 0
        if feed_count >= max_feeds:
            raise HTTPException(
                status_code=403,
                detail=f"Your plan allows up to {max_feeds} feeds. Upgrade to subscribe to more.",
            )

    feed = Feed(url=feed_url, title=payload.title, user_id=current_user.id)
    db.add(feed)
    db.flush()

    if payload.category_ids:
        _apply_categories(feed, payload.category_ids, current_user, db)

    db.commit()
    db.refresh(feed)

    try:
        await refresh_feed(feed, db)
        db.refresh(feed)
    except Exception as exc:
        logger.warning("Initial feed refresh failed for feed %s (%s): %s", feed.id, feed_url, exc)

    return _build_feed_response(feed, db)


@router.get("/health", response_model=FeedListResponse, summary="List feeds sorted by health (worst first)")
def list_feeds_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feeds = (
        db.query(Feed)
        .filter(Feed.user_id == current_user.id)
        .options(selectinload(Feed.categories), defer(Feed.description))
        .order_by(Feed.fetch_failure_count.desc(), Feed.last_success_at.asc().nullsfirst())
        .limit(500)
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
        .options(
            selectinload(Feed.categories),
            defer(Feed.description),   # not displayed in sidebar or feed list UI
        )
    )
    if active_only:
        q = q.filter(Feed.is_active == True)  # noqa: E712
    if category_id is not None:
        q = q.filter(Feed.categories.any(id=category_id))
    # must_read first, then casual, then archive_only; created_at desc within each tier
    tier_order = case(
        (Feed.importance_tier == "must_read", 0),
        (Feed.importance_tier == "casual", 1),
        (Feed.importance_tier == "archive_only", 2),
        else_=1,
    )
    feeds = q.order_by(tier_order, Feed.created_at.desc()).limit(500).all()
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
    if payload.auto_mark_read is not None:
        feed.auto_mark_read = payload.auto_mark_read
    if payload.default_open_action is not None:
        feed.default_open_action = payload.default_open_action
    if payload.importance_tier is not None:
        feed.importance_tier = payload.importance_tier
    if payload.manual_refresh_only is not None:
        feed.manual_refresh_only = payload.manual_refresh_only
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
        new_count = await refresh_feed(feed, db, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {exc}")
    return RefreshResult(feed_id=feed_id, new_articles=new_count, message=f"Fetched {new_count} new article(s)")
