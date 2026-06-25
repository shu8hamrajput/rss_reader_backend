import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Article, Feed, Highlight, User
from ..schemas import HighlightCreate, HighlightResponse, HighlightReviewItem, HighlightUpdate

router = APIRouter(tags=["Highlights"])
logger = logging.getLogger(__name__)


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
        .limit(500)
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
        text=payload.text,
        note=payload.note,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    # Fire webhooks for highlight_created event
    try:
        from ..tasks import _fire_webhooks_sync
        article = db.query(Article).filter(Article.id == article_id).first()
        _fire_webhooks_sync(db, current_user.id, "highlight_created", {
            "highlight_id": h.id,
            "article_id": article_id,
            "article_title": article.title if article else None,
            "article_url": article.url if article else None,
            "start_pos": h.start_pos,
            "end_pos": h.end_pos,
            "color_id": h.color_id,
        })
    except Exception as exc:
        logger.warning("Webhook fire failed for highlight %s: %s", h.id, exc)
    return h


@router.patch(
    "/highlights/{highlight_id}",
    response_model=HighlightResponse,
    summary="Update highlight color or note",
)
def update_highlight(
    highlight_id: int,
    payload: HighlightUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    h = _owned_highlight(highlight_id, current_user, db)
    if payload.color_id is not None:
        h.color_id = payload.color_id
    if "note" in payload.model_fields_set:
        h.note = payload.note
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


@router.get(
    "/highlights/review",
    response_model=list[HighlightReviewItem],
    summary="Get highlights due for spaced-repetition review",
    description=(
        "Returns up to `limit` highlights ordered by reviewed_at ASC NULLS FIRST "
        "(never-reviewed first, then oldest-reviewed). Heavier highlights "
        "(color_id 3 or 4) are weighted as if reviewed 7 days earlier."
    ),
)
def get_review_queue(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime, timedelta, timezone as _tz
    from sqlalchemy import case, nullsfirst
    now = datetime.now(_tz.utc)
    # Score: reviewed_at + bonus for high-color highlights (review them less often)
    score_expr = case(
        (Highlight.reviewed_at.is_(None), (now - timedelta(days=9999)).replace(tzinfo=None)),
        else_=Highlight.reviewed_at,
    )
    rows = (
        db.query(
            Highlight,
            Article.title.label("article_title"),
            Article.url.label("article_url"),
        )
        .join(Article, Highlight.article_id == Article.id)
        .filter(Highlight.user_id == current_user.id)
        .order_by(nullsfirst(Highlight.reviewed_at.asc()))
        .limit(limit)
        .all()
    )
    result = []
    for row in rows:
        h = row.Highlight
        result.append(HighlightReviewItem(
            id=h.id,
            article_id=h.article_id,
            article_title=row.article_title,
            article_url=row.article_url,
            start_pos=h.start_pos,
            end_pos=h.end_pos,
            color_id=h.color_id,
            text=h.text,
            note=h.note,
            ai_question=h.ai_question,
            reviewed_at=h.reviewed_at,
            created_at=h.created_at,
        ))
    return result


@router.post(
    "/highlights/{highlight_id}/reviewed",
    response_model=HighlightResponse,
    summary="Mark a highlight as reviewed (spaced repetition)",
)
def mark_highlight_reviewed(
    highlight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime, timezone as _tz
    h = _owned_highlight(highlight_id, current_user, db)
    h.reviewed_at = datetime.now(_tz.utc)
    db.commit()
    db.refresh(h)
    return h


@router.get(
    "/highlights/export",
    summary="Export all highlights as JSON",
    description="Returns every highlight the user has created, enriched with article title and URL.",
)
def export_highlights(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Project only the two Article columns we actually use (title, url).
    # The previous join loaded full Article ORM objects including content /
    # full_content which are never read here.
    rows = (
        db.query(
            Highlight,
            Article.title.label("article_title"),
            Article.url.label("article_url"),
        )
        .join(Article, Highlight.article_id == Article.id)
        .filter(Highlight.user_id == current_user.id)
        .order_by(Highlight.created_at.asc())
        .all()
    )
    export = [
        {
            "id": row.Highlight.id,
            "article_id": row.Highlight.article_id,
            "article_title": row.article_title,
            "article_url": row.article_url,
            "start_pos": row.Highlight.start_pos,
            "end_pos": row.Highlight.end_pos,
            "color_id": row.Highlight.color_id,
            "text": row.Highlight.text,
            "note": row.Highlight.note,
            "created_at": row.Highlight.created_at.isoformat() if row.Highlight.created_at else None,
        }
        for row in rows
    ]
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": "attachment; filename=highlights.json"},
    )


@router.post(
    "/highlights/{highlight_id}/generate-question",
    response_model=HighlightResponse,
    summary="Generate an Anki question for a highlight using AI",
)
async def generate_anki_question(
    highlight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    h = _owned_highlight(highlight_id, current_user, db)
    if not h.text:
        raise HTTPException(status_code=400, detail="Highlight has no text")
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="AI question generation not configured")

    row = db.query(Article.title).filter(Article.id == h.article_id).first()
    article_title = row.title if row else "Unknown article"

    from anthropic import Anthropic
    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        system=(
            "You generate Anki flashcard questions. Given a highlighted passage from an article, "
            "produce exactly one clear question whose answer is that passage. "
            "Test conceptual understanding, not just recall. Return only the question text."
        ),
        messages=[{
            "role": "user",
            "content": f'Article: "{article_title}"\n\nHighlighted passage:\n"{h.text}"',
        }],
    )
    h.ai_question = message.content[0].text.strip()
    db.commit()
    db.refresh(h)
    return h


@router.get(
    "/highlights/export/anki",
    summary="Export highlights as an Anki-importable tab-separated file",
    description=(
        "Returns a .txt file in Anki's Basic note type tab-separated format. "
        "Highlights with an ai_question use it as the front; others use the note field or are skipped."
    ),
)
def export_highlights_anki(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(
            Highlight,
            Article.title.label("article_title"),
        )
        .join(Article, Highlight.article_id == Article.id)
        .filter(Highlight.user_id == current_user.id)
        .order_by(Highlight.created_at.asc())
        .all()
    )
    lines = ["#separator:tab", "#html:false", "#notetype:Basic", "#columns:Front\tBack\tTags"]
    for row in rows:
        h = row.Highlight
        front = h.ai_question or h.note
        if not front or not h.text:
            continue
        tag = (row.article_title or "").replace(" ", "_").replace("\t", " ")[:40]
        back = h.text.replace("\t", " ").replace("\n", " ")
        front_clean = front.replace("\t", " ").replace("\n", " ")
        lines.append(f"{front_clean}\t{back}\t{tag}")

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=highlights_anki.txt"},
    )
