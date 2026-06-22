from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import ArticleRule, User
from ..schemas import RuleCreate, RuleResponse, RuleUpdate
from ..services.plans import effective_plan, limits_for

router = APIRouter(prefix="/rules", tags=["Rules"])


@router.get("", response_model=list[RuleResponse])
def list_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(ArticleRule)
        .filter(ArticleRule.user_id == current_user.id)
        .order_by(ArticleRule.order, ArticleRule.id)
        .limit(200)
        .all()
    )


@router.post("", response_model=RuleResponse, status_code=201)
def create_rule(
    payload: RuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = effective_plan(current_user)
    limits = limits_for(plan)
    if limits.max_rules is not None:
        count = db.query(ArticleRule).filter(ArticleRule.user_id == current_user.id).count()
        if count >= limits.max_rules:
            raise HTTPException(
                status_code=403,
                detail=f"Free plan allows {limits.max_rules} rules. Upgrade to Pro for unlimited.",
            )
    if not payload.conditions:
        raise HTTPException(status_code=422, detail="At least one condition is required.")
    if not payload.actions:
        raise HTTPException(status_code=422, detail="At least one action is required.")

    rule = ArticleRule(
        user_id=current_user.id,
        name=payload.name.strip(),
        conditions=[c.model_dump() for c in payload.conditions],
        actions=[a.model_dump() for a in payload.actions],
        is_active=payload.is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=RuleResponse)
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = (
        db.query(ArticleRule)
        .filter(ArticleRule.id == rule_id, ArticleRule.user_id == current_user.id)
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404)
    if payload.name is not None:
        rule.name = payload.name.strip()
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.conditions is not None:
        if not payload.conditions:
            raise HTTPException(status_code=422, detail="At least one condition is required.")
        rule.conditions = [c.model_dump() for c in payload.conditions]
    if payload.actions is not None:
        if not payload.actions:
            raise HTTPException(status_code=422, detail="At least one action is required.")
        rule.actions = [a.model_dump() for a in payload.actions]
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = (
        db.query(ArticleRule)
        .filter(ArticleRule.id == rule_id, ArticleRule.user_id == current_user.id)
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404)
    db.delete(rule)
    db.commit()
