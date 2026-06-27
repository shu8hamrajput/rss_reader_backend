import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import Collection, CollectionItem, CollectionSubscription, Feed, User
from ..schemas import (
    CollectionCreate,
    CollectionItemCreate,
    CollectionItemResponse,
    CollectionListResponse,
    CollectionResponse,
    CollectionSubscribeResult,
    CollectionUpdate,
)
from ..services.plans import effective_plan, limits_for

router = APIRouter(prefix="/collections", tags=["Collections"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "collection"


def _unique_slug(name: str, owner_id: int, db: Session) -> str:
    base = _slugify(name)
    slug = base
    n = 2
    while db.query(Collection).filter(Collection.owner_id == owner_id, Collection.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _get_subscribed_collection_ids(user_id: int, db: Session) -> set[int]:
    rows = db.query(CollectionSubscription.collection_id).filter(CollectionSubscription.user_id == user_id).limit(10_000).all()
    return {r.collection_id for r in rows}


def _build_response(collection: Collection, current_user: User, db: Session, subscribed_ids: set[int] | None = None) -> CollectionResponse:
    if subscribed_ids is not None:
        is_subscribed = collection.id in subscribed_ids
    else:
        is_subscribed = (
            db.query(CollectionSubscription)
            .filter(CollectionSubscription.collection_id == collection.id, CollectionSubscription.user_id == current_user.id)
            .first()
            is not None
        )
    data = CollectionResponse.model_validate(collection)
    data.owner_name = collection.owner.name
    data.is_subscribed = is_subscribed
    data.is_owner = collection.owner_id == current_user.id
    data.items = [CollectionItemResponse.model_validate(i) for i in collection.items]
    return data


def _owned_collection(collection_id: int, user: User, db: Session) -> Collection:
    collection = (
        db.query(Collection)
        .options(selectinload(Collection.owner), selectinload(Collection.items))
        .filter(Collection.id == collection_id, Collection.owner_id == user.id)
        .first()
    )
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


def _visible_collection(collection_id: int, user: User, db: Session) -> Collection:
    collection = (
        db.query(Collection)
        .options(selectinload(Collection.owner), selectinload(Collection.items))
        .filter(Collection.id == collection_id)
        .first()
    )
    if not collection or (not collection.is_public and collection.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED, summary="Create a collection")
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = Collection(
        owner_id=current_user.id,
        name=payload.name,
        slug=_unique_slug(payload.name, current_user.id, db),
        description=payload.description,
        is_public=payload.is_public,
    )
    db.add(collection)
    db.flush()

    seen: set[str] = set()
    for idx, item in enumerate(payload.items):
        norm = _normalize_url(item.feed_url)
        if norm in seen:
            continue
        seen.add(norm)
        db.add(CollectionItem(
            collection_id=collection.id,
            feed_url=item.feed_url.strip(),
            title=item.title,
            icon_url=item.icon_url,
            position=idx,
        ))

    db.commit()
    db.refresh(collection)
    return _build_response(collection, current_user, db)


@router.get("/mine", response_model=list[CollectionResponse], summary="List collections you own")
def list_my_collections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collections = (
        db.query(Collection)
        .options(selectinload(Collection.owner), selectinload(Collection.items))
        .filter(Collection.owner_id == current_user.id)
        .order_by(Collection.created_at.desc())
        .limit(200)
        .all()
    )
    subscribed_ids = _get_subscribed_collection_ids(current_user.id, db)
    return [_build_response(c, current_user, db, subscribed_ids) for c in collections]


@router.get("/subscribed", response_model=list[CollectionResponse], summary="List collections you're subscribed to")
def list_subscribed_collections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collections = (
        db.query(Collection)
        .options(selectinload(Collection.owner), selectinload(Collection.items))
        .join(CollectionSubscription, CollectionSubscription.collection_id == Collection.id)
        .filter(CollectionSubscription.user_id == current_user.id)
        .order_by(CollectionSubscription.subscribed_at.desc())
        .limit(200)
        .all()
    )
    subscribed_ids = _get_subscribed_collection_ids(current_user.id, db)
    return [_build_response(c, current_user, db, subscribed_ids) for c in collections]


@router.get("/discover", response_model=CollectionListResponse, summary="Discover public collections")
def discover_collections(
    search: str | None = Query(None, description="Search by name or description"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Collection).options(selectinload(Collection.owner), selectinload(Collection.items)).filter(Collection.is_public == True)
    if search:
        term = f"%{search}%"
        q = q.filter(or_(Collection.name.ilike(term), Collection.description.ilike(term)))

    total = q.count()
    items = (
        q.order_by(Collection.subscriber_count.desc(), Collection.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    subscribed_ids = _get_subscribed_collection_ids(current_user.id, db)
    return CollectionListResponse(
        total=total, page=page, page_size=page_size,
        items=[_build_response(c, current_user, db, subscribed_ids) for c in items],
    )


@router.get("/{collection_id}", response_model=CollectionResponse, summary="Get a collection")
def get_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _visible_collection(collection_id, current_user, db)
    return _build_response(collection, current_user, db)


@router.patch("/{collection_id}", response_model=CollectionResponse, summary="Update a collection")
def update_collection(
    collection_id: int,
    payload: CollectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _owned_collection(collection_id, current_user, db)
    if payload.name is not None and payload.name.strip() and payload.name.strip() != collection.name:
        collection.name = payload.name.strip()
        collection.slug = _unique_slug(collection.name, current_user.id, db)
    if payload.description is not None:
        collection.description = payload.description
    if payload.is_public is not None:
        collection.is_public = payload.is_public
    db.commit()
    collection = _owned_collection(collection_id, current_user, db)
    return _build_response(collection, current_user, db)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a collection")
def delete_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _owned_collection(collection_id, current_user, db)
    db.delete(collection)
    db.commit()


@router.post(
    "/{collection_id}/items",
    response_model=CollectionItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a feed to a collection",
)
def add_collection_item(
    collection_id: int,
    payload: CollectionItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _owned_collection(collection_id, current_user, db)
    norm = _normalize_url(payload.feed_url)
    for existing in collection.items:
        if _normalize_url(existing.feed_url) == norm:
            raise HTTPException(status_code=409, detail="This feed is already in the collection")

    max_position = db.query(func.max(CollectionItem.position)).filter(CollectionItem.collection_id == collection.id).scalar()
    item = CollectionItem(
        collection_id=collection.id,
        feed_url=payload.feed_url.strip(),
        title=payload.title,
        icon_url=payload.icon_url,
        position=(max_position or 0) + 1,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return CollectionItemResponse.model_validate(item)


@router.delete(
    "/{collection_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a feed from a collection",
)
def remove_collection_item(
    collection_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _owned_collection(collection_id, current_user, db)
    item = db.query(CollectionItem).filter(CollectionItem.id == item_id, CollectionItem.collection_id == collection.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()


@router.post("/{collection_id}/subscribe", response_model=CollectionSubscribeResult, summary="Subscribe to a collection")
async def subscribe_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    collection = _visible_collection(collection_id, current_user, db)

    existing = (
        db.query(CollectionSubscription)
        .filter(CollectionSubscription.collection_id == collection.id, CollectionSubscription.user_id == current_user.id)
        .first()
    )
    if existing:
        return CollectionSubscribeResult(subscribed=True, feeds_added=0)

    url_rows = db.query(Feed.url).filter(Feed.user_id == current_user.id).limit(10_000).all()
    existing_urls = {_normalize_url(r.url) for r in url_rows}
    max_feeds = limits_for(effective_plan(current_user)).max_feeds
    feed_count = len(existing_urls)
    feeds_added = 0

    for item in collection.items:
        norm = _normalize_url(item.feed_url)
        if norm in existing_urls:
            continue
        if max_feeds is not None and feed_count >= max_feeds:
            break
        feed = Feed(url=item.feed_url, title=item.title, user_id=current_user.id)
        db.add(feed)
        existing_urls.add(norm)
        feed_count += 1
        feeds_added += 1

    db.add(CollectionSubscription(collection_id=collection.id, user_id=current_user.id))
    db.query(Collection).filter(Collection.id == collection.id).update(
        {"subscriber_count": Collection.subscriber_count + 1}
    )
    db.commit()
    return CollectionSubscribeResult(subscribed=True, feeds_added=feeds_added)


@router.delete("/{collection_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT, summary="Unsubscribe from a collection")
def unsubscribe_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = (
        db.query(CollectionSubscription)
        .filter(CollectionSubscription.collection_id == collection_id, CollectionSubscription.user_id == current_user.id)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="You are not subscribed to this collection")

    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    db.delete(sub)
    if collection:
        db.query(Collection).filter(
            Collection.id == collection_id, Collection.subscriber_count > 0
        ).update({"subscriber_count": Collection.subscriber_count - 1})
    db.commit()
