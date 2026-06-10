import pytest

from app.redis_client import redis_client
from app.services.usage import _fetch_usage_key, record_fetches, remaining_fetch_quota


@pytest.fixture(autouse=True)
def _cleanup_usage_key(user):
    yield
    redis_client.delete(_fetch_usage_key(user.id))


def test_remaining_fetch_quota_free_plan_no_usage(user):
    assert remaining_fetch_quota(user) == 20


def test_record_fetches_decrements_remaining_quota(user):
    record_fetches(user, 5)
    assert remaining_fetch_quota(user) == 15


def test_record_fetches_clamped_at_zero(user):
    record_fetches(user, 100)
    assert remaining_fetch_quota(user) == 0


def test_paid_plan_unlimited_quota_and_record_is_noop(db_session, user):
    user.plan = "paid"
    db_session.commit()

    assert remaining_fetch_quota(user) is None

    record_fetches(user, 5)
    assert redis_client.get(_fetch_usage_key(user.id)) is None
