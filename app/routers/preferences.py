"""
User preferences endpoints.

GET  /me/preferences  — returns the stored preferences blob (or {} if none yet)
PUT  /me/preferences  — accepts any JSON body, upserts the user_preferences row,
                        returns saved preferences

Preferences are stored in a dedicated user_preferences table (one row per user)
so they can be synced across devices without touching the users table.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import User, UserPreferences
from ..schemas import PreferencesResponse

router = APIRouter(prefix="/me", tags=["Preferences"])


@router.get(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Get the current user's stored preferences",
)
def get_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(UserPreferences).filter(UserPreferences.user_id == current_user.id).first()
    if row is None:
        return PreferencesResponse(preferences={})
    return PreferencesResponse(preferences=row.preferences, updated_at=row.updated_at)


@router.put(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Upsert the current user's preferences",
    description=(
        "Accepts any JSON object. The entire blob is replaced (not merged). "
        "Client owns the schema — backend stores it opaquely."
    ),
)
async def put_preferences(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    if not isinstance(body, dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    row = db.query(UserPreferences).filter(UserPreferences.user_id == current_user.id).first()
    if row is None:
        row = UserPreferences(user_id=current_user.id, preferences=body)
        db.add(row)
    else:
        row.preferences = body
    db.commit()
    db.refresh(row)
    return PreferencesResponse(preferences=row.preferences, updated_at=row.updated_at)
