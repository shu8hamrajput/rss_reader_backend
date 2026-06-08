"""
Subscription plans and their usage limits.

Limits are set against the two cost drivers that scale with individual user
behaviour rather than aggregate platform size:

- `max_feeds`                  — DB rows + periodic refresh load
- `daily_full_content_fetches` — outbound scrape requests (refetch / bulk
  save-later), the most expensive per-action operation in the app

`None` means unlimited.

Paid access is time-boxed (`User.plan_expires_at`) and renewed via Razorpay
checkout — see `services.razorpay_client` and `routers.payments`. Once it
lapses, `effective_plan()` treats the user as "free" again without needing a
background job to flip `User.plan` back.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models import User


@dataclass(frozen=True)
class PlanLimits:
    max_feeds: int | None
    daily_full_content_fetches: int | None


@dataclass(frozen=True)
class PlanPricing:
    amount: int  # smallest currency unit (paise for INR)
    currency: str
    duration_days: int


DEFAULT_PLAN = "free"

PLAN_LIMITS: dict[str, PlanLimits] = {
    "free": PlanLimits(max_feeds=25, daily_full_content_fetches=20),
    "paid": PlanLimits(max_feeds=None, daily_full_content_fetches=None),
}

# Plans purchasable via Razorpay — keyed the same as PLAN_LIMITS.
# ₹299/month, billed as a single one-time order (renewed manually via checkout).
PLAN_PRICING: dict[str, PlanPricing] = {
    "paid": PlanPricing(amount=29900, currency="INR", duration_days=30),
}


def limits_for(plan: str) -> PlanLimits:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])


def effective_plan(user: User) -> str:
    """The plan to enforce right now — falls back to "free" once a paid plan's
    `plan_expires_at` has passed, without requiring a background job to reset it."""
    if user.plan != DEFAULT_PLAN and user.plan_expires_at is not None:
        if user.plan_expires_at < datetime.now(timezone.utc):
            return DEFAULT_PLAN
    return user.plan
