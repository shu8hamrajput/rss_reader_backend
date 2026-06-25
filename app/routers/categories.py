from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Category, Feed, User
from ..schemas import CategoryCreate, CategoryResponse, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["Categories"])


def _owned_category(category_id: int, user: User, db: Session) -> Category:
    cat = db.query(Category).filter(Category.id == category_id, Category.user_id == user.id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


def _to_response(cat: Category) -> CategoryResponse:
    r = CategoryResponse.model_validate(cat)
    r.feed_count = len(cat.feeds)
    return r


@router.get("", response_model=list[CategoryResponse], summary="List your categories")
def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cats = db.query(Category).filter(Category.user_id == current_user.id).order_by(Category.name).limit(200).all()
    return [_to_response(c) for c in cats]


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a category",
)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(Category).filter(
        Category.user_id == current_user.id, Category.name == payload.name
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Category name already exists")
    cat = Category(user_id=current_user.id, name=payload.name)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return _to_response(cat)


@router.get("/{category_id}", response_model=CategoryResponse, summary="Get a category")
def get_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _to_response(_owned_category(category_id, current_user, db))


@router.patch("/{category_id}", response_model=CategoryResponse, summary="Rename a category")
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = _owned_category(category_id, current_user, db)
    conflict = db.query(Category).filter(
        Category.user_id == current_user.id,
        Category.name == payload.name,
        Category.id != category_id,
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="Category name already exists")
    cat.name = payload.name
    db.commit()
    db.refresh(cat)
    return _to_response(cat)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a category (feeds are not deleted)",
)
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = _owned_category(category_id, current_user, db)
    db.delete(cat)
    db.commit()


@router.post(
    "/{category_id}/feeds/{feed_id}",
    response_model=CategoryResponse,
    summary="Add a feed to a category",
)
def add_feed_to_category(
    category_id: int,
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = _owned_category(category_id, current_user, db)
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == current_user.id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed not in cat.feeds:
        cat.feeds.append(feed)
        db.commit()
        db.refresh(cat)
    return _to_response(cat)


@router.delete(
    "/{category_id}/feeds/{feed_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a feed from a category",
)
def remove_feed_from_category(
    category_id: int,
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = _owned_category(category_id, current_user, db)
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == current_user.id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed in cat.feeds:
        cat.feeds.remove(feed)
        db.commit()
