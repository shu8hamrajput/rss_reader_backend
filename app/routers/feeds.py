from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Category, Feed, User
from ..schemas import (
    CategoryResponse,
    FeedCreate,
    FeedListResponse,
    FeedResponse,
    FeedUpdate,
    RefreshResult,
)
from ..services.feed_parser import refresh_feed

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
        .filter(Article.feed_id == feed.id, Article.is_read == False)
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


@router.get("", response_model=FeedListResponse, summary="List your subscribed feeds")
def list_feeds(
    active_only: bool = Query(False),
    category_id: int | None = Query(None, description="Filter by category ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Feed).filter(Feed.user_id == current_user.id)
    if active_only:
        q = q.filter(Feed.is_active == True)
    if category_id is not None:
        q = q.filter(Feed.categories.any(id=category_id))
    feeds = q.order_by(Feed.created_at.desc()).all()
    return FeedListResponse(total=len(feeds), items=[_build_feed_response(f, db) for f in feeds])


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
