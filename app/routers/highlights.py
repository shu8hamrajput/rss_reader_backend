from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Feed, Highlight, User
from ..schemas import HighlightCreate, HighlightResponse, HighlightUpdate

router = APIRouter(tags=["Highlights"])


def _owned_highlight(highlight_id: int, user: User, db: Session) -> Highlight:
    h = (
        db.query(Highlight)
        .filter(Highlight.id == highlight_id, Highlight.user_id == user.id)
        .first()
    )
    if not h:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return h


def _owned_article_id(article_id: int, user: User, db: Session) -> None:
    exists = (
        db.query(Article.id)
        .join(Feed)
        .filter(Article.id == article_id, Feed.user_id == user.id)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Article not found")


@router.get(
    "/articles/{article_id}/highlights",
    response_model=list[HighlightResponse],
    summary="List highlights for an article",
)
def list_highlights(
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_article_id(article_id, current_user, db)
    return (
        db.query(Highlight)
        .filter(Highlight.article_id == article_id, Highlight.user_id == current_user.id)
        .order_by(Highlight.start_pos)
        .all()
    )


@router.post(
    "/articles/{article_id}/highlights",
    response_model=HighlightResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a highlight on an article",
)
def create_highlight(
    article_id: int,
    payload: HighlightCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_article_id(article_id, current_user, db)
    h = Highlight(
        user_id=current_user.id,
        article_id=article_id,
        start_pos=payload.start_pos,
        end_pos=payload.end_pos,
        color_id=payload.color_id,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@router.patch(
    "/highlights/{highlight_id}",
    response_model=HighlightResponse,
    summary="Update highlight color",
)
def update_highlight(
    highlight_id: int,
    payload: HighlightUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    h = _owned_highlight(highlight_id, current_user, db)
    h.color_id = payload.color_id
    db.commit()
    db.refresh(h)
    return h


@router.delete(
    "/highlights/{highlight_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a highlight",
)
def delete_highlight(
    highlight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    h = _owned_highlight(highlight_id, current_user, db)
    db.delete(h)
    db.commit()
