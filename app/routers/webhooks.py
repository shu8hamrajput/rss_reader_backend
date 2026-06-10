import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import User, UserWebhook
from ..schemas import WebhookCreate, WebhookResponse

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _owned(webhook_id: int, user: User, db: Session) -> UserWebhook:
    w = db.query(UserWebhook).filter(
        UserWebhook.id == webhook_id, UserWebhook.user_id == user.id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return w


@router.get("", response_model=list[WebhookResponse], summary="List your webhooks")
def list_webhooks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(UserWebhook).filter(UserWebhook.user_id == current_user.id).order_by(UserWebhook.created_at.asc()).all()


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED, summary="Create a webhook")
def create_webhook(
    payload: WebhookCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(UserWebhook).filter(
        UserWebhook.user_id == current_user.id,
        UserWebhook.url == payload.url,
    ).first()
    if existing:
        return existing
    w = UserWebhook(
        user_id=current_user.id,
        url=payload.url,
        events=json.dumps(payload.events),
        secret=payload.secret,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


@router.patch("/{webhook_id}", response_model=WebhookResponse, summary="Toggle webhook active state")
def toggle_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    w = _owned(webhook_id, current_user, db)
    w.is_active = not w.is_active
    db.commit()
    db.refresh(w)
    return w


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a webhook")
def delete_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.delete(_owned(webhook_id, current_user, db))
    db.commit()
