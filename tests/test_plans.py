from datetime import datetime, timedelta, timezone

from app.services.plans import PLAN_LIMITS, effective_plan, limits_for


def test_limits_for_known_plans():
    assert limits_for("free") == PLAN_LIMITS["free"]
    assert limits_for("paid") == PLAN_LIMITS["paid"]


def test_limits_for_unknown_plan_falls_back_to_free():
    assert limits_for("unknown") == PLAN_LIMITS["free"]


def test_effective_plan_free_regardless_of_expiry(db_session, user):
    user.plan = "free"
    user.plan_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert effective_plan(user) == "free"


def test_effective_plan_paid_not_yet_expired(db_session, user):
    user.plan = "paid"
    user.plan_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    assert effective_plan(user) == "paid"


def test_effective_plan_paid_expired_falls_back_to_free(db_session, user):
    user.plan = "paid"
    user.plan_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert effective_plan(user) == "free"


def test_effective_plan_paid_no_expiry_treated_as_non_expiring(db_session, user):
    user.plan = "paid"
    user.plan_expires_at = None
    assert effective_plan(user) == "paid"
