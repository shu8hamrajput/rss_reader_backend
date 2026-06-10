from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import SearchAlert, User
from ..schemas import SearchAlertCreate, SearchAlertResponse

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=list[SearchAlertResponse], summary="List search alerts")
def list_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(SearchAlert).filter(SearchAlert.user_id == current_user.id).order_by(SearchAlert.created_at).all()


@router.post("", response_model=SearchAlertResponse, status_code=status.HTTP_201_CREATED, summary="Create a search alert")
def create_alert(
    payload: SearchAlertCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(SearchAlert).filter(
        SearchAlert.user_id == current_user.id,
        SearchAlert.query == payload.query,
    ).first()
    if existing:
        return existing
    alert = SearchAlert(user_id=current_user.id, query=payload.query, label=payload.label)
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a search alert")
def delete_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = db.query(SearchAlert).filter(
        SearchAlert.id == alert_id,
        SearchAlert.user_id == current_user.id,
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()
