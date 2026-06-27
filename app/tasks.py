"""
Celery tasks — periodic feed refresh (replaces the old in-process APScheduler job with a Celery-beat-driven schedule).

Runs in a separate worker process on its own DB session; the existing async
fetch/parse pipeline is driven via asyncio.run(). New-article counts are
published to Redis so connected SSE clients still get live updates.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from .celery_app import celery_app
from .config import settings
from .database import SessionLocal
from .models import AlertMatch, Article, ArticleRule, Feed, GeneratedCandidate, SearchAlert, UserWebhook
from .services.events import publish
from .services.feed_parser import refresh_url_for_all_subscribers
from .services.fetchers._common import strip_and_select

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────────────────────

def _tokenize(title: str) -> set[str]:
    """Lowercase alphanumeric tokens, length >= 3, for Jaccard similarity."""
    return {t for t in re.findall(r'[a-z0-9]+', title.lower()) if len(t) >= 3}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_stories(db, articles: list[Article]) -> None:
    """Assign story_cluster_id to new articles that share a topic with same-day articles.

    Uses Jaccard similarity on title tokens. Articles with similarity >= 0.4
    are grouped under the same UUID cluster. Already-clustered nearby articles
    are pulled from DB to allow expansion of existing clusters.
    """
    if not articles:
        return

    since = datetime.now(timezone.utc) - timedelta(hours=36)
    nearby_rows = db.query(Article.id, Article.title, Article.story_cluster_id).filter(
        Article.published_at >= since,
        Article.id.notin_([a.id for a in articles]),
    ).limit(5000).all()

    # Build lookup: {id: (title_tokens, cluster_id)}
    existing: list[tuple[int, set[str], str | None]] = [
        (row.id, _tokenize(row.title or ""), row.story_cluster_id)
        for row in nearby_rows
    ]

    for article in articles:
        tokens = _tokenize(article.title or "")
        best_cluster: str | None = None
        best_score = 0.39  # threshold
        scores: list[float] = []

        for _, etokens, ecluster in existing:
            score = _jaccard(tokens, etokens)
            scores.append(score)
            if score > best_score:
                best_score = score
                best_cluster = ecluster

        if best_cluster is None and best_score > 0.39:
            best_cluster = str(uuid.uuid4())

        if best_cluster:
            article.story_cluster_id = best_cluster
            # Persist cluster_id to existing unclustered articles that matched —
            # previously only the in-memory list was updated, leaving those rows
            # with story_cluster_id=NULL in the DB.
            newly_clustered_ids = [
                eid for i, (eid, _, ecluster) in enumerate(existing)
                if not ecluster and scores[i] > 0.39
            ]
            if newly_clustered_ids:
                db.query(Article).filter(Article.id.in_(newly_clustered_ids)).update(
                    {"story_cluster_id": best_cluster}, synchronize_session=False
                )
            existing = [
                (eid, etokens, best_cluster if scores[i] > 0.39 and not ecluster else ecluster)
                for i, (eid, etokens, ecluster) in enumerate(existing)
            ]
        else:
            article.story_cluster_id = None


def _update_feed_velocity(db, feed: Feed, new_count: int) -> None:
    """Update rolling 7-day articles/day average; mark noisy if it spikes > 3x avg."""
    if feed.articles_per_day_avg is None:
        feed.articles_per_day_avg = float(new_count)
    else:
        # Exponential moving average with ~7-day window (alpha = 2/8)
        feed.articles_per_day_avg = 0.75 * feed.articles_per_day_avg + 0.25 * float(new_count)


def _tag_list(article: Article) -> list[str]:
    if not article.tags:
        return []
    try:
        parsed = json.loads(article.tags)
        return parsed if isinstance(parsed, list) else []
    except Exception as exc:
        logger.debug("Could not parse tags for article %d: %s", article.id, exc)
        return []


def _estimate_read_time(article: Article) -> int:
    text = " ".join(filter(None, [
        article.title or "",
        article.summary or "",
        article.content or "",
        article.full_content or "",
    ]))
    return max(1, round(len(text.split()) / 200))


def _condition_matches(article: Article, cond: dict) -> bool:
    field = cond.get("field", "")
    op = cond.get("op", "")
    val = cond.get("value", "")
    if field in ("title", "author", "content"):
        if field == "title":
            target = (article.title or "").lower()
        elif field == "author":
            target = (article.author or "").lower()
        else:
            target = " ".join(filter(None, [
                article.title or "", article.summary or "",
                article.content or "", article.full_content or "",
            ])).lower()
        v = str(val).lower()
        if op == "contains":
            return v in target
        if op == "not_contains":
            return v not in target
    elif field == "feed_id":
        try:
            v_int = int(val)
        except (TypeError, ValueError):
            return False
        if op == "eq":
            return article.feed_id == v_int
        if op == "neq":
            return article.feed_id != v_int
    elif field == "read_time_min":
        rt = _estimate_read_time(article)
        try:
            v_int = int(val)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return rt > v_int
        if op == "lt":
            return rt < v_int
        if op == "eq":
            return rt == v_int
        if op == "neq":
            return rt != v_int
    return False


def _apply_rule_actions(article: Article, actions: list[dict]) -> None:
    for action in actions:
        atype = action.get("type")
        val = action.get("value")
        if atype == "add_tag":
            tags = _tag_list(article)
            tag_str = str(val).strip()
            if tag_str and tag_str not in tags:
                tags.append(tag_str)
                article.tags = json.dumps(tags)
        elif atype == "mark_read":
            article.is_read = True
            article.read_at = datetime.now(timezone.utc)
        elif atype == "bookmark":
            article.is_bookmarked = True
        elif atype == "read_later":
            tags = _tag_list(article)
            if "saved_later" not in tags:
                tags.append("saved_later")
                article.tags = json.dumps(tags)


def _apply_rules(db, user_id: int, articles: list[Article], cached_rules: list | None = None) -> None:
    if not articles:
        return
    rules = cached_rules if cached_rules is not None else (
        db.query(ArticleRule)
        .filter(ArticleRule.user_id == user_id, ArticleRule.is_active == True)
        .order_by(ArticleRule.order, ArticleRule.id)
        .all()
    )
    if not rules:
        return
    for article in articles:
        for rule in rules:
            conditions = rule.conditions or []
            if conditions and all(_condition_matches(article, c) for c in conditions):
                _apply_rule_actions(article, rule.actions or [])
                rule.match_count = (rule.match_count or 0) + 1


def _match_alerts(db, feed_id: int, user_id: int, since: datetime, cached_alerts: list | None = None, cached_webhooks: list | None = None) -> None:
    """Check newly ingested articles against the user's search alerts and publish matches."""
    alerts = cached_alerts if cached_alerts is not None else db.query(SearchAlert).filter(SearchAlert.user_id == user_id).all()
    if not alerts:
        return
    for alert in alerts:
        try:
            article_ids = [
                row[0]
                for row in db.execute(
                    text("""
                        SELECT id FROM articles
                        WHERE feed_id = :feed_id
                          AND created_at >= :since
                          AND search_vector @@ websearch_to_tsquery('english', :q)
                    """),
                    {"feed_id": feed_id, "since": since, "q": alert.query},
                ).fetchall()
            ]
            count = len(article_ids)
            if count > 0:
                alert.last_matched_at = datetime.now(timezone.utc)
                db.add(AlertMatch(
                    alert_id=alert.id,
                    feed_id=feed_id,
                    article_ids=json.dumps(article_ids),
                    count=count,
                ))
                publish(user_id, {
                    "type": "search_alert",
                    "alert_id": alert.id,
                    "query": alert.query,
                    "label": alert.label,
                    "count": count,
                    "feed_id": feed_id,
                })
                _fire_webhooks_sync(db, user_id, "alert_matched", {
                    "alert_id": alert.id,
                    "query": alert.query,
                    "count": count,
                    "feed_id": feed_id,
                }, cached_webhooks=cached_webhooks)
        except Exception as exc:
            logger.warning("Alert match failed for alert %d: %s", alert.id, exc)


def _fire_webhooks_sync(db, user_id: int, event: str, payload: dict, cached_webhooks: list | None = None) -> None:
    """Deliver event payload to all active webhooks subscribed to this event type."""
    webhooks = cached_webhooks if cached_webhooks is not None else db.query(UserWebhook).filter(
        UserWebhook.user_id == user_id,
        UserWebhook.is_active == True,
    ).all()
    for wh in webhooks:
        try:
            events = json.loads(wh.events or "[]")
        except Exception as exc:
            logger.debug("Failed to parse webhook events for webhook %d: %s", wh.id, exc)
            events = []
        if event not in events:
            continue
        body = json.dumps({"event": event, "data": payload}).encode()
        headers = {"Content-Type": "application/json", "X-RSS-Event": event}
        if wh.secret:
            sig = hmac.new(wh.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-RSS-Signature"] = f"sha256={sig}"
        try:
            # Fire-and-forget sync; failures are logged and do not block the task
            httpx.post(wh.url, content=body, headers=headers, timeout=5.0)
            wh.last_fired_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.warning("Webhook %d delivery failed: %s", wh.id, exc)


# ── Celery tasks ────────────────────────────────────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.refresh_all_feeds")
def refresh_all_feeds() -> None:
    """Refresh every active feed, grouped by URL so shared feeds are fetched once."""
    db = SessionLocal()
    try:
        feeds = db.query(Feed).filter(Feed.is_active == True).all()

        url_groups: dict[str, list[Feed]] = defaultdict(list)
        for feed in feeds:
            url_groups[feed.url].append(feed)

        logger.info(
            "Celery beat: refreshing %d unique URL(s) across %d active feed(s)",
            len(url_groups), len(feeds),
        )

        # Pre-fetch rules, alerts, and webhooks per user to avoid repeated identical
        # queries when multiple feeds of the same user refresh in the same beat cycle.
        user_ids = {feed.user_id for feed in feeds}
        rules_by_user: dict[int, list] = {
            uid: db.query(ArticleRule)
                .filter(ArticleRule.user_id == uid, ArticleRule.is_active == True)
                .order_by(ArticleRule.order, ArticleRule.id)
                .all()
            for uid in user_ids
        }
        alerts_by_user: dict[int, list] = {
            uid: db.query(SearchAlert).filter(SearchAlert.user_id == uid).all()
            for uid in user_ids
        }
        webhooks_by_user: dict[int, list] = {
            uid: db.query(UserWebhook).filter(UserWebhook.user_id == uid, UserWebhook.is_active == True).all()
            for uid in user_ids
        }

        for url, feed_group in url_groups.items():
            try:
                before_refresh = datetime.now(timezone.utc)
                results = asyncio.run(refresh_url_for_all_subscribers(feed_group, db))
                now = datetime.now(timezone.utc)
                for feed in feed_group:
                    feed.fetch_failure_count = 0
                    feed.last_success_at = now
                    new_count = results.get(feed.id, 0)
                    _update_feed_velocity(db, feed, new_count)
                    if new_count > 0:
                        # Cluster new articles for this feed
                        new_articles = db.query(Article).filter(
                            Article.feed_id == feed.id,
                            Article.created_at >= before_refresh,
                        ).all()
                        _cluster_stories(db, new_articles)
                        _apply_rules(db, feed.user_id, new_articles, rules_by_user.get(feed.user_id))
                        publish(
                            feed.user_id,
                            {"type": "new_articles", "feed_id": feed.id, "count": new_count},
                        )
                        _match_alerts(db, feed.id, feed.user_id, before_refresh, alerts_by_user.get(feed.user_id), webhooks_by_user.get(feed.user_id))
                        _fire_webhooks_sync(db, feed.user_id, "new_article", {
                            "feed_id": feed.id,
                            "count": new_count,
                        }, cached_webhooks=webhooks_by_user.get(feed.user_id))
                db.commit()
            except Exception as exc:
                logger.warning("Failed to refresh URL %s: %s", url, exc)
                for feed in feed_group:
                    feed.fetch_failure_count += 1
                db.commit()
    except Exception as exc:
        logger.error("refresh_all_feeds task failed: %s", exc, exc_info=True)
    finally:
        db.close()


@celery_app.task(name="app.tasks.refresh_feed_by_id")
def refresh_feed_by_id(feed_id: int) -> int:
    """Refresh a single feed by ID. Returns the new-article count."""
    db = SessionLocal()
    try:
        feed = db.query(Feed).filter(Feed.id == feed_id).first()
        if not feed:
            return 0
        try:
            before_refresh = datetime.now(timezone.utc)
            results = asyncio.run(refresh_url_for_all_subscribers([feed], db))
            feed.fetch_failure_count = 0
            feed.last_success_at = datetime.now(timezone.utc)
            new_count = results.get(feed.id, 0)
            _update_feed_velocity(db, feed, new_count)
            db.commit()
            if new_count > 0:
                new_articles = db.query(Article).filter(
                    Article.feed_id == feed.id,
                    Article.created_at >= before_refresh,
                ).all()
                _cluster_stories(db, new_articles)
                _apply_rules(db, feed.user_id, new_articles)
                db.commit()
                publish(
                    feed.user_id,
                    {"type": "new_articles", "feed_id": feed.id, "count": new_count},
                )
                _match_alerts(db, feed.id, feed.user_id, before_refresh)
                _fire_webhooks_sync(db, feed.user_id, "new_article", {
                    "feed_id": feed.id,
                    "count": new_count,
                })
                db.commit()
            return new_count
        except Exception as exc:
            logger.warning("Failed to refresh feed %d: %s", feed_id, exc)
            feed.fetch_failure_count += 1
            db.commit()
            return 0
    except Exception as exc:
        logger.error("refresh_feed_by_id task failed for feed %d: %s", feed_id, exc, exc_info=True)
        return 0
    finally:
        db.close()


def _generate_candidate(db, candidate: "GeneratedCandidate", url: str, use_llm: bool, samples_n: int = 3) -> None:
    """Generate (or refine) a parser_gen fetcher candidate for *candidate*'s domain.

    Shared by the `generate_fetcher_candidate` Celery task and tests, which pass
    in their own session.
    """
    from .services.parser_gen import codegen
    from .services.parser_gen import samples as pg_samples
    from .services.parser_gen.__main__ import _gather_samples, _propose
    from .services.parser_gen.proposal import SelectorProposal

    try:
        article_urls, _ = pg_samples.sample_article_urls(url, samples_n)
        fetched = _gather_samples(article_urls)
        if not fetched:
            raise RuntimeError("could not fetch any sample pages")

        existing_path = codegen.candidate_path(candidate.slug)
        if not existing_path.exists():
            existing_path = codegen.active_path(candidate.slug)

        current = None
        iteration = 1
        pattern = codegen.domain_pattern(candidate.domain)
        before_selectors: tuple = ()
        before_noise: tuple = ()
        if existing_path.exists():
            attrs = codegen.load_module_attrs(existing_path)
            current = SelectorProposal(
                content_selectors=list(attrs["content_selectors"]),
                noise_selectors=list(attrs["noise_selectors"]),
                reasoning=attrs["meta"].get("reasoning", ""),
            )
            iteration = int(attrs["meta"].get("iteration", 1)) + 1
            pattern = attrs["domain_pattern"] or pattern
            before_selectors = attrs["content_selectors"]
            before_noise = attrs["noise_selectors"]

        html_samples = [html for _, html in fetched]
        proposal = _propose(html_samples, use_llm, current=current)

        before_chars: dict[str, int] = {}
        after_chars: dict[str, int] = {}
        for sample_url, html in fetched:
            before = strip_and_select(html, before_selectors, before_noise) if before_selectors else None
            after = strip_and_select(html, tuple(proposal.content_selectors), tuple(proposal.noise_selectors))
            before_chars[sample_url] = len(before) if before else 0
            after_chars[sample_url] = len(after) if after else 0

        meta = {
            "domain": candidate.domain,
            "feed_url": url,
            "sample_urls": [u for u, _ in fetched],
            "mode": "llm" if use_llm else "heuristic",
            "model": settings.parser_gen_model if use_llm else None,
            "reasoning": proposal.reasoning,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "before_chars": before_chars,
            "after_chars": after_chars,
        }
        source = codegen.render_module(proposal, meta, pattern)
        codegen.write_candidate(candidate.slug, source)

        candidate.status = "ready"
        candidate.mode = meta["mode"]
        candidate.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        candidate.status = "failed"
        candidate.error = str(exc)[:500]
        candidate.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise


@celery_app.task(name="app.tasks.generate_fetcher_candidate")
def generate_fetcher_candidate(candidate_id: int, url: str, use_llm: bool, samples_n: int = 3) -> None:
    """Sync task — parser_gen's samples/heuristics/llm helpers are sync, and an
    LLM call can take 10-30s which would block the Uvicorn worker if run inline."""
    db = SessionLocal()
    try:
        candidate = db.query(GeneratedCandidate).filter(GeneratedCandidate.id == candidate_id).first()
        if not candidate:
            return
        _generate_candidate(db, candidate, url, use_llm, samples_n)
    finally:
        db.close()
