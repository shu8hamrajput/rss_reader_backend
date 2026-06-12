from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import FeatureVote, User
from ..schemas import FeatureVoteCreate, FeatureVoteResponse

router = APIRouter(prefix="/feature-votes", tags=["Feature Votes"])


@router.get("", response_model=list[str], summary="List feature keys the current user has voted for")
def list_feature_votes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    votes = (
        db.query(FeatureVote)
        .filter(FeatureVote.user_id == current_user.id)
        .order_by(FeatureVote.created_at)
        .all()
    )
    return [v.feature_key for v in votes]


@router.post("", response_model=FeatureVoteResponse, status_code=status.HTTP_201_CREATED, summary="Vote for a roadmap feature")
def create_feature_vote(
    payload: FeatureVoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(FeatureVote).filter(
        FeatureVote.user_id == current_user.id,
        FeatureVote.feature_key == payload.feature_key,
    ).first()
    if existing:
        return existing
    vote = FeatureVote(user_id=current_user.id, feature_key=payload.feature_key)
    db.add(vote)
    db.commit()
    db.refresh(vote)
    return vote
