from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer,
    String, Table, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import JSON
from sqlalchemy import func
from sqlalchemy import TIMESTAMP

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Many-to-many: feeds ↔ categories ─────────────────────────────────────────

feed_categories = Table(
    "feed_categories",
    Base.metadata,
    Column("feed_id", Integer, ForeignKey("feeds.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    google_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(256))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    # Subscription plan — gates feed-count and full-content-fetch limits (see services.plans)
    plan: Mapped[str] = mapped_column(String(32), default="free", server_default="free", nullable=False)
    # When a paid plan lapses, effective_plan() falls back to "free" — see services.plans
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cross-device sync for client-side preferences (theme, layout, default view,
    # reader font, saved searches, ...) — keyed by top-level section, e.g.
    # {"settings": {...}, "layout": {...}, "saved_searches": [...]}
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0', default=0)
    api_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # IANA timezone name (e.g. "America/New_York"); drives per-feed quiet hours.
    # A first-class, validated column rather than the opaque `preferences` blob,
    # since backend logic (quiet-hours webhook gating) depends on its correctness.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False, server_default="'UTC'")

    feeds: Mapped[list["Feed"]] = relationship("Feed", back_populates="user", cascade="all, delete-orphan")
    categories: Mapped[list["Category"]] = relationship("Category", back_populates="user", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    collections: Mapped[list["Collection"]] = relationship(
        "Collection", back_populates="owner", cascade="all, delete-orphan", foreign_keys="Collection.owner_id"
    )
    search_alerts: Mapped[list["SearchAlert"]] = relationship("SearchAlert", back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    webhooks: Mapped[list["UserWebhook"]] = relationship("UserWebhook", back_populates="user", cascade="all, delete-orphan")


# ── Categories ────────────────────────────────────────────────────────────────

class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_category_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="categories")
    feeds: Mapped[list["Feed"]] = relationship("Feed", secondary=feed_categories, back_populates="categories")


# ── Feeds ─────────────────────────────────────────────────────────────────────

class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (UniqueConstraint("url", "user_id", name="uq_feed_url_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    site_url: Mapped[str | None] = mapped_column(String(2048))
    icon_url: Mapped[str | None] = mapped_column(String(2048))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # HTTP caching headers stored from last successful fetch
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetch_failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default='0')
    last_success_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    health_snooze_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Rolling 7-day average articles/day — used for velocity anomaly detection
    articles_per_day_avg: Mapped[Optional[float]] = mapped_column(nullable=True)
    # Which plugin handles this feed (set on first successful fetch)
    plugin_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Auto-mark ingested articles as read instead of unread — for low-signal feeds skimmed but never opened
    auto_mark_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    # What clicking an article from this feed does: "reader" | "original" | "list"
    default_open_action: Mapped[str] = mapped_column(String(16), default="reader", nullable=False, server_default="'reader'")
    # User-set priority: "must_read" | "casual" | "archive_only" — drives sort order and badge emphasis
    importance_tier: Mapped[str] = mapped_column(String(16), default="casual", nullable=False, server_default="'casual'")
    # Excludes this feed from the scheduled Celery beat refresh — only fetched via manual "Refresh now"
    manual_refresh_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    # User's freeform note on why they follow this feed — an aid for later pruning decisions
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # User-chosen accent color for sidebar/list grouping, hex like "#3b82f6"
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Once true, _apply_feed_meta stops overwriting icon_url from the source feed —
    # set automatically when the user provides a custom icon_url via PATCH
    icon_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    # Pinned feeds surface above category ordering regardless of which folder they're in
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    # Whether new articles get full-content fetched (and stored) at ingest time.
    # When False, full_content stays empty until the reader explicitly requests
    # it via refetch/save-later — for feeds where the fetched HTML isn't wanted
    # by default (e.g. paywalled sites, or to save fetch-quota headroom).
    auto_full_content: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default='true')
    # When True, new articles whose (normalized) title already exists among this
    # user's other feeds are skipped on ingest — for aggregators/syndicators that
    # repost content the user already follows at its original source.
    suppress_duplicates: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    # Overrides the global ~30-minute beat cadence for this feed specifically.
    # Null means "use the global default" — the beat schedule refreshes it every cycle.
    refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When set, the daily prune task deletes this feed's articles older than N days —
    # unless bookmarked or highlighted, which are always kept regardless of age.
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When set, the daily prune task keeps only the newest N non-bookmarked,
    # non-highlighted articles for this feed — evicting the oldest excess.
    max_articles_retained: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When False, new articles from this feed never trigger "new_article" webhook
    # deliveries — for high-volume/low-priority feeds where instant push isn't
    # wanted. SSE live-notification and alert matching are unaffected.
    webhook_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default='true')
    # Comma-separated keywords. Mute: matching new articles (title/summary,
    # case-insensitive substring) are skipped entirely on ingest. Boost: matching
    # articles get a "boosted" tag added instead of being filtered.
    mute_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)
    boost_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # When set, new articles whose combined title+summary+content length falls
    # short are skipped on ingest — filters out link-only stubs and teaser posts.
    min_content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set when the user starts a trial subscription for this feed. The daily prune
    # task deactivates the feed once this passes, unless explicitly kept (cleared)
    # first — lowers the cost of trying a feed without committing to it forever.
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Records how this feed was added — "manual" | "search" | "onboarding" |
    # "opml_import" | "collection" — set once at creation, not user-editable.
    # Lets feeds be grouped by discovery source rather than only by category.
    discovered_via: Mapped[str] = mapped_column(String(32), default="manual", nullable=False, server_default="'manual'")
    # Quiet hours (0-23, in the owning user's timezone). When both are set,
    # "new_article" webhook delivery for this feed is suppressed during the
    # window — SSE live-notification and alert matching are unaffected, same
    # scope as webhook_eligible. Wraps midnight when start > end.
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="feeds")
    articles: Mapped[list["Article"]] = relationship(
        "Article", back_populates="feed", cascade="all, delete-orphan"
    )
    categories: Mapped[list["Category"]] = relationship(
        "Category", secondary=feed_categories, back_populates="feeds"
    )


# ── Articles ──────────────────────────────────────────────────────────────────

class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    feed_id: Mapped[int] = mapped_column(Integer, ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    guid: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(1024))
    url: Mapped[str | None] = mapped_column(String(2048))
    author: Mapped[str | None] = mapped_column(String(256))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_bookmarked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # JSON array of user-defined tag strings, e.g. ["read_later","saved_later"]
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resume_at_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    itunes_author: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Reading depth (0-100 scroll %) — cross-device resume for text articles
    scroll_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Story cluster UUID — articles covering the same event share this ID
    story_cluster_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    # User-written note attached to the whole article (independent of highlights)
    article_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Postgres full-text search vector (title + summary + content), kept in
    # sync by a DB trigger — see _migrate() in main.py
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    feed: Mapped["Feed"] = relationship("Feed", back_populates="articles")
    highlights: Mapped[list["Highlight"]] = relationship(
        "Highlight", back_populates="article", cascade="all, delete-orphan"
    )


# ── Highlights ────────────────────────────────────────────────────────────────

class Highlight(Base):
    __tablename__ = "highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    article_id: Mapped[int] = mapped_column(Integer, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    start_pos: Mapped[int] = mapped_column(Integer, nullable=False)
    end_pos: Mapped[int] = mapped_column(Integer, nullable=False)
    color_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Spaced repetition — timestamp of last review; NULL means never reviewed
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    article: Mapped["Article"] = relationship("Article", back_populates="highlights")


# ── Payments (Razorpay) ───────────────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # smallest currency unit (paise for INR)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    razorpay_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_signature: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # created → paid → (or failed); set by /payments/verify or the webhook
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="payments")


# ── Collections (curated, shareable feed lists) ──────────────────────────────

class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("owner_id", "slug", name="uq_collection_owner_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Denormalized for cheap sorting/display — kept in sync on subscribe/unsubscribe
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    owner: Mapped["User"] = relationship("User", back_populates="collections", foreign_keys=[owner_id])
    items: Mapped[list["CollectionItem"]] = relationship(
        "CollectionItem", back_populates="collection", cascade="all, delete-orphan", order_by="CollectionItem.position"
    )
    subscriptions: Mapped[list["CollectionSubscription"]] = relationship(
        "CollectionSubscription", back_populates="collection", cascade="all, delete-orphan"
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "feed_url", name="uq_collection_item_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    feed_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped["Collection"] = relationship("Collection", back_populates="items")


# ── User Preferences (separate table for per-user JSON blob) ──────────────────

class UserPreferences(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class CollectionSubscription(Base):
    __tablename__ = "collection_subscriptions"
    __table_args__ = (UniqueConstraint("collection_id", "user_id", name="uq_collection_subscription"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    collection_id: Mapped[int] = mapped_column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped["Collection"] = relationship("Collection", back_populates="subscriptions")
    user: Mapped["User"] = relationship("User")


# ── Search Alerts ─────────────────────────────────────────────────────────────

# ── User Sessions ─────────────────────────────────────────────────────────────

class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_info: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="sessions")


class SearchAlert(Base):
    __tablename__ = "search_alerts"
    __table_args__ = (UniqueConstraint("user_id", "query", name="uq_search_alert_user_query"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="search_alerts")
    matches: Mapped[list["AlertMatch"]] = relationship(
        "AlertMatch", back_populates="alert", cascade="all, delete-orphan",
        order_by="AlertMatch.matched_at.desc()",
    )


class AlertMatch(Base):
    """A batch of articles that matched a search alert during one feed refresh."""
    __tablename__ = "alert_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    feed_id: Mapped[int] = mapped_column(Integer, ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False)
    article_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    alert: Mapped["SearchAlert"] = relationship("SearchAlert", back_populates="matches")


# ── Parser requests ──────────────────────────────────────────────────────────

class ParserRequest(Base):
    """A user's request for a better content extractor for an article's domain.

    Picked up by `python -m app.services.parser_gen process-requests`, which
    generates/refines a candidate fetcher for the domain and marks matching
    requests as processed.
    """
    __tablename__ = "parser_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    article_id: Mapped[int] = mapped_column(Integer, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Generated fetcher candidates (self-healing feeds) ───────────────────────────

class GeneratedCandidate(Base):
    """A job ticket for a parser_gen-generated fetcher candidate for a feed's domain.

    Proposal content (selectors, reasoning, before/after char counts) is not
    duplicated here — it's read on demand from the candidate `.py` file via
    `codegen.load_module_attrs()`.
    """
    __tablename__ = "generated_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    feed_id: Mapped[int] = mapped_column(Integer, ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)  # pending|ready|approved|failed
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # heuristic|llm
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── User Webhooks ─────────────────────────────────────────────────────────────

class UserWebhook(Base):
    __tablename__ = "user_webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # JSON array of event types: ["new_article", "highlight_created", "alert_matched"]
    events: Mapped[str] = mapped_column(Text, nullable=False, default='["new_article"]')
    # HMAC secret for payload signing; NULL = no signing
    secret: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="webhooks")


# ── Article routing rules ─────────────────────────────────────────────────────

class ArticleRule(Base):
    __tablename__ = "article_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # JSON list of condition objects: [{field, op, value}, ...]
    conditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # JSON list of action objects: [{type, value?}, ...]
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    match_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Feature votes ─────────────────────────────────────────────────────────────

class FeatureVote(Base):
    __tablename__ = "feature_votes"
    __table_args__ = (UniqueConstraint("user_id", "feature_key", name="uq_feature_vote_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
