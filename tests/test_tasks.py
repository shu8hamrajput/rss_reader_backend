import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models import AlertMatch, Article, ArticleRule, Highlight, SearchAlert, UserWebhook
from app.tasks import (
    _apply_rule_actions,
    _apply_rules,
    _cluster_stories,
    _condition_matches,
    _estimate_read_time,
    _expire_trial_feeds,
    _fire_webhooks_sync,
    _is_due_for_refresh,
    _jaccard,
    _match_alerts,
    _prune_excess_articles,
    _prune_expired_articles,
    _tag_list,
    _tokenize,
    _update_feed_velocity,
)

from .conftest import make_article, make_feed


def test_tokenize_lowercases_strips_punctuation_and_drops_short_tokens():
    assert _tokenize("Hello, World! It's 2024") == {"hello", "world", "2024"}


def test_jaccard():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_cluster_stories_no_similar_articles_gets_no_cluster(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(
        db_session, feed,
        title="Local Bakery Wins Award For Best Croissant",
        published_at=datetime.now(timezone.utc),
    )

    _cluster_stories(db_session, [article])

    assert article.story_cluster_id is None


def test_cluster_stories_groups_similar_titles_under_new_cluster(db_session, user):
    feed = make_feed(db_session, user)
    title = "Major Earthquake Strikes Northern California Region"
    older = make_article(
        db_session, feed,
        title=title,
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    new1 = make_article(db_session, feed, title=title, published_at=datetime.now(timezone.utc))
    new2 = make_article(db_session, feed, title=title, published_at=datetime.now(timezone.utc))

    _cluster_stories(db_session, [new1, new2])
    db_session.expire(older)  # bulk UPDATE below doesn't sync the ORM identity map

    assert new1.story_cluster_id is not None
    assert new1.story_cluster_id == new2.story_cluster_id
    # older is matched and unclustered, so it's persisted into the same cluster too —
    # otherwise it would be left behind with story_cluster_id=NULL forever.
    assert older.story_cluster_id == new1.story_cluster_id


def test_cluster_stories_inherits_existing_cluster_id(db_session, user):
    feed = make_feed(db_session, user)
    title = "Major Earthquake Strikes Northern California Region"
    make_article(
        db_session, feed,
        title=title,
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        story_cluster_id="existing-uuid-1234",
    )
    new = make_article(db_session, feed, title=title, published_at=datetime.now(timezone.utc))

    _cluster_stories(db_session, [new])

    assert new.story_cluster_id == "existing-uuid-1234"


def test_update_feed_velocity_first_and_subsequent_calls(db_session, user):
    feed = make_feed(db_session, user)
    assert feed.articles_per_day_avg is None

    _update_feed_velocity(db_session, feed, 10)
    assert feed.articles_per_day_avg == 10.0

    _update_feed_velocity(db_session, feed, 2)
    assert feed.articles_per_day_avg == 8.0


def test_is_due_for_refresh_no_override_always_due(db_session, user):
    feed = make_feed(db_session, user, refresh_interval_minutes=None, last_fetched_at=datetime.now(timezone.utc))
    assert _is_due_for_refresh(feed, datetime.now(timezone.utc)) is True


def test_is_due_for_refresh_never_fetched_is_due(db_session, user):
    feed = make_feed(db_session, user, refresh_interval_minutes=120, last_fetched_at=None)
    assert _is_due_for_refresh(feed, datetime.now(timezone.utc)) is True


def test_is_due_for_refresh_before_interval_elapsed_not_due(db_session, user):
    now = datetime.now(timezone.utc)
    feed = make_feed(db_session, user, refresh_interval_minutes=120, last_fetched_at=now - timedelta(minutes=30))
    assert _is_due_for_refresh(feed, now) is False


def test_is_due_for_refresh_after_interval_elapsed_is_due(db_session, user):
    now = datetime.now(timezone.utc)
    feed = make_feed(db_session, user, refresh_interval_minutes=120, last_fetched_at=now - timedelta(minutes=121))
    assert _is_due_for_refresh(feed, now) is True


def test_prune_expired_articles_deletes_past_retention_window(db_session, user):
    feed = make_feed(db_session, user, retention_days=30)
    old = make_article(db_session, feed, guid="old", created_at=datetime.now(timezone.utc) - timedelta(days=31))
    recent = make_article(db_session, feed, guid="recent", created_at=datetime.now(timezone.utc) - timedelta(days=1))

    deleted = _prune_expired_articles(db_session)

    assert deleted == 1
    remaining_guids = {a.guid for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert remaining_guids == {"recent"}


def test_prune_expired_articles_keeps_bookmarked(db_session, user):
    feed = make_feed(db_session, user, retention_days=30)
    make_article(db_session, feed, guid="old-bookmarked", created_at=datetime.now(timezone.utc) - timedelta(days=60), is_bookmarked=True)

    deleted = _prune_expired_articles(db_session)

    assert deleted == 0
    assert db_session.query(Article).filter(Article.feed_id == feed.id).count() == 1


def test_prune_expired_articles_keeps_highlighted(db_session, user):
    feed = make_feed(db_session, user, retention_days=30)
    old = make_article(db_session, feed, guid="old-highlighted", created_at=datetime.now(timezone.utc) - timedelta(days=60))
    db_session.add(Highlight(user_id=user.id, article_id=old.id, start_pos=0, end_pos=10, color_id=1))
    db_session.commit()

    deleted = _prune_expired_articles(db_session)

    assert deleted == 0
    assert db_session.query(Article).filter(Article.feed_id == feed.id).count() == 1


def test_prune_expired_articles_ignores_feeds_without_retention(db_session, user):
    feed = make_feed(db_session, user, retention_days=None)
    make_article(db_session, feed, guid="ancient", created_at=datetime.now(timezone.utc) - timedelta(days=3650))

    deleted = _prune_expired_articles(db_session)

    assert deleted == 0


def test_prune_excess_articles_evicts_oldest_beyond_cap(db_session, user):
    feed = make_feed(db_session, user, max_articles_retained=2)
    oldest = make_article(db_session, feed, guid="oldest", created_at=datetime.now(timezone.utc) - timedelta(days=3))
    middle = make_article(db_session, feed, guid="middle", created_at=datetime.now(timezone.utc) - timedelta(days=2))
    newest = make_article(db_session, feed, guid="newest", created_at=datetime.now(timezone.utc) - timedelta(days=1))

    deleted = _prune_excess_articles(db_session)

    assert deleted == 1
    remaining_guids = {a.guid for a in db_session.query(Article).filter(Article.feed_id == feed.id).all()}
    assert remaining_guids == {"middle", "newest"}


def test_prune_excess_articles_keeps_bookmarked_out_of_cap(db_session, user):
    feed = make_feed(db_session, user, max_articles_retained=1)
    make_article(db_session, feed, guid="bookmarked", created_at=datetime.now(timezone.utc) - timedelta(days=10), is_bookmarked=True)
    make_article(db_session, feed, guid="newest", created_at=datetime.now(timezone.utc) - timedelta(days=1))

    deleted = _prune_excess_articles(db_session)

    assert deleted == 0
    assert db_session.query(Article).filter(Article.feed_id == feed.id).count() == 2


def test_prune_excess_articles_keeps_highlighted_out_of_cap(db_session, user):
    feed = make_feed(db_session, user, max_articles_retained=1)
    old = make_article(db_session, feed, guid="highlighted", created_at=datetime.now(timezone.utc) - timedelta(days=10))
    db_session.add(Highlight(user_id=user.id, article_id=old.id, start_pos=0, end_pos=10, color_id=1))
    make_article(db_session, feed, guid="newest", created_at=datetime.now(timezone.utc) - timedelta(days=1))
    db_session.commit()

    deleted = _prune_excess_articles(db_session)

    assert deleted == 0
    assert db_session.query(Article).filter(Article.feed_id == feed.id).count() == 2


def test_prune_excess_articles_ignores_feeds_without_cap(db_session, user):
    feed = make_feed(db_session, user, max_articles_retained=None)
    for i in range(5):
        make_article(db_session, feed, guid=f"g{i}")

    deleted = _prune_excess_articles(db_session)

    assert deleted == 0


def test_expire_trial_feeds_deactivates_past_expiry(db_session, user):
    feed = make_feed(db_session, user, trial_expires_at=datetime.now(timezone.utc) - timedelta(days=1), is_active=True)

    expired_count = _expire_trial_feeds(db_session)

    db_session.refresh(feed)
    assert expired_count == 1
    assert feed.is_active is False
    assert feed.trial_expires_at is None


def test_expire_trial_feeds_leaves_unexpired_trials_alone(db_session, user):
    feed = make_feed(db_session, user, trial_expires_at=datetime.now(timezone.utc) + timedelta(days=5), is_active=True)

    expired_count = _expire_trial_feeds(db_session)

    db_session.refresh(feed)
    assert expired_count == 0
    assert feed.is_active is True
    assert feed.trial_expires_at is not None


def test_expire_trial_feeds_ignores_feeds_without_trial(db_session, user):
    feed = make_feed(db_session, user, trial_expires_at=None, is_active=True)

    expired_count = _expire_trial_feeds(db_session)

    db_session.refresh(feed)
    assert expired_count == 0
    assert feed.is_active is True


def test_tag_list(db_session, user):
    feed = make_feed(db_session, user)
    assert _tag_list(make_article(db_session, feed, tags=None)) == []
    assert _tag_list(make_article(db_session, feed, tags="not json")) == []
    assert _tag_list(make_article(db_session, feed, tags=json.dumps(["a", "b"]))) == ["a", "b"]


def test_estimate_read_time(db_session, user):
    feed = make_feed(db_session, user)
    empty = make_article(db_session, feed, title="", summary="", content="")
    assert _estimate_read_time(empty) == 1

    long_article = make_article(db_session, feed, title="", summary="", content=" ".join(["word"] * 400))
    assert _estimate_read_time(long_article) == 2


def test_condition_matches(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(
        db_session, feed,
        title="Apple Reports Record Quarterly Revenue Growth",
        author="John Doe",
        summary="",
        content="",
        full_content="special keyword here",
    )

    assert _condition_matches(article, {"field": "title", "op": "contains", "value": "Apple"}) is True
    assert _condition_matches(article, {"field": "title", "op": "not_contains", "value": "Banana"}) is True
    assert _condition_matches(article, {"field": "author", "op": "contains", "value": "john"}) is True
    assert _condition_matches(article, {"field": "content", "op": "contains", "value": "special"}) is True
    assert _condition_matches(article, {"field": "feed_id", "op": "eq", "value": article.feed_id}) is True
    assert _condition_matches(article, {"field": "feed_id", "op": "neq", "value": article.feed_id + 1}) is True

    read_time = _estimate_read_time(article)
    assert _condition_matches(article, {"field": "read_time_min", "op": "eq", "value": read_time}) is True
    assert _condition_matches(article, {"field": "read_time_min", "op": "gt", "value": read_time - 1}) is True
    assert _condition_matches(article, {"field": "read_time_min", "op": "lt", "value": read_time + 1}) is True
    assert _condition_matches(article, {"field": "read_time_min", "op": "neq", "value": read_time + 1}) is True

    assert _condition_matches(article, {"field": "bogus", "op": "contains", "value": "x"}) is False
    assert _condition_matches(article, {"field": "title", "op": "bogus", "value": "x"}) is False


def test_apply_rule_actions(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, tags=None, is_read=False, is_bookmarked=False)

    _apply_rule_actions(article, [{"type": "add_tag", "value": "tech"}])
    assert json.loads(article.tags) == ["tech"]

    _apply_rule_actions(article, [{"type": "add_tag", "value": "tech"}])
    assert json.loads(article.tags) == ["tech"]  # no dup

    _apply_rule_actions(article, [{"type": "mark_read"}])
    assert article.is_read is True
    assert article.read_at is not None

    _apply_rule_actions(article, [{"type": "bookmark"}])
    assert article.is_bookmarked is True

    _apply_rule_actions(article, [{"type": "read_later"}])
    assert "saved_later" in json.loads(article.tags)


def test_apply_rules_active_rule_with_matching_conditions(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, title="Apple Reports Earnings", tags=None)

    rule = ArticleRule(
        user_id=user.id,
        name="Tag Apple news",
        is_active=True,
        conditions=[{"field": "title", "op": "contains", "value": "apple"}],
        actions=[{"type": "add_tag", "value": "tech"}],
    )
    db_session.add(rule)
    db_session.commit()

    _apply_rules(db_session, user.id, [article])

    assert "tech" in json.loads(article.tags)
    assert rule.match_count == 1


def test_apply_rules_inactive_rule_not_applied(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, title="Apple Reports Earnings", tags=None)

    rule = ArticleRule(
        user_id=user.id,
        name="Inactive rule",
        is_active=False,
        conditions=[{"field": "title", "op": "contains", "value": "apple"}],
        actions=[{"type": "add_tag", "value": "tech"}],
    )
    db_session.add(rule)
    db_session.commit()

    _apply_rules(db_session, user.id, [article])

    assert article.tags is None
    assert rule.match_count == 0


def test_apply_rules_empty_conditions_not_applied(db_session, user):
    feed = make_feed(db_session, user)
    article = make_article(db_session, feed, title="Apple Reports Earnings", tags=None)

    rule = ArticleRule(
        user_id=user.id,
        name="No conditions",
        is_active=True,
        conditions=[],
        actions=[{"type": "add_tag", "value": "tech"}],
    )
    db_session.add(rule)
    db_session.commit()

    _apply_rules(db_session, user.id, [article])

    assert article.tags is None
    assert rule.match_count == 0


def test_match_alerts_publishes_and_fires_webhooks_on_match(db_session, user):
    feed = make_feed(db_session, user)
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    article = make_article(
        db_session, feed,
        title="Rust Async Programming Guide",
        summary="Learn rust async patterns",
        content="",
    )

    alert = SearchAlert(user_id=user.id, query="rust async")
    db_session.add(alert)
    db_session.commit()

    with patch("app.tasks.publish") as mock_publish, \
            patch("app.tasks._fire_webhooks_sync") as mock_fire:
        _match_alerts(db_session, feed.id, user.id, since)

    mock_publish.assert_called_once()
    assert mock_publish.call_args[0][1]["type"] == "search_alert"
    mock_fire.assert_called_once()
    assert mock_fire.call_args[0][2] == "alert_matched"

    assert alert.last_matched_at is not None

    match = db_session.query(AlertMatch).filter(AlertMatch.alert_id == alert.id).one()
    assert match.feed_id == feed.id
    assert match.count == 1
    assert json.loads(match.article_ids) == [article.id]


def test_match_alerts_no_match_does_nothing(db_session, user):
    feed = make_feed(db_session, user)
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    make_article(db_session, feed, title="Completely unrelated headline", summary="", content="")

    alert = SearchAlert(user_id=user.id, query="nonexistent topic xyz")
    db_session.add(alert)
    db_session.commit()

    with patch("app.tasks.publish") as mock_publish, \
            patch("app.tasks._fire_webhooks_sync") as mock_fire:
        _match_alerts(db_session, feed.id, user.id, since)

    mock_publish.assert_not_called()
    mock_fire.assert_not_called()

    db_session.refresh(alert)
    assert alert.last_matched_at is None


def test_fire_webhooks_sync_active_subscribed_webhook(db_session, user):
    webhook = UserWebhook(
        user_id=user.id,
        url="https://example.com/hook",
        events=json.dumps(["new_article"]),
        secret="mysecret",
        is_active=True,
    )
    db_session.add(webhook)
    db_session.commit()

    with patch("app.tasks.httpx.post") as mock_post:
        _fire_webhooks_sync(db_session, user.id, "new_article", {"feed_id": 1, "count": 2})

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert mock_post.call_args[0][0] == "https://example.com/hook"
    body = kwargs["content"]
    expected_sig = hmac.new(b"mysecret", body, hashlib.sha256).hexdigest()
    assert kwargs["headers"]["X-RSS-Signature"] == f"sha256={expected_sig}"

    assert webhook.last_fired_at is not None


def test_fire_webhooks_sync_skips_unsubscribed_event(db_session, user):
    webhook = UserWebhook(
        user_id=user.id,
        url="https://example.com/hook",
        events=json.dumps(["alert_matched"]),
        is_active=True,
    )
    db_session.add(webhook)
    db_session.commit()

    with patch("app.tasks.httpx.post") as mock_post:
        _fire_webhooks_sync(db_session, user.id, "new_article", {"feed_id": 1, "count": 2})

    mock_post.assert_not_called()


def test_fire_webhooks_sync_swallows_delivery_errors(db_session, user):
    webhook = UserWebhook(
        user_id=user.id,
        url="https://example.com/hook",
        events=json.dumps(["new_article"]),
        is_active=True,
    )
    db_session.add(webhook)
    db_session.commit()

    with patch("app.tasks.httpx.post", side_effect=Exception("boom")):
        _fire_webhooks_sync(db_session, user.id, "new_article", {"feed_id": 1, "count": 2})

    assert webhook.last_fired_at is None
