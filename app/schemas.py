import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, computed_field, field_validator, model_validator, ConfigDict

from .auth import admin_emails


# ── Auth / User schemas ───────────────────────────────────────────────────────

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    avatar_url: str | None
    plan: str
    created_at: datetime
    last_login_at: datetime
    # Synced client preferences (theme, layout, default view, reader font, saved
    # searches, ...) — opaque to the backend, merged shallowly on update
    preferences: dict | None = None
    api_token: str | None = None

    @computed_field
    @property
    def is_admin(self) -> bool:
        return self.email.lower() in admin_emails()


class ApiTokenResponse(BaseModel):
    api_token: str


# ── Search Alert schemas ──────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_info: str | None
    ip_address: str | None
    created_at: datetime
    last_seen_at: datetime


class SearchAlertCreate(BaseModel):
    query: str
    label: str | None = None


class SearchAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    query: str
    label: str | None
    created_at: datetime
    last_matched_at: datetime | None = None


class AlertMatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    feed_id: int
    article_ids: list[int]
    count: int
    matched_at: datetime

    @field_validator("article_ids", mode="before")
    @classmethod
    def parse_article_ids(cls, v) -> list[int]:
        if isinstance(v, str):
            return json.loads(v)
        return v


class ParserRequestCreate(BaseModel):
    note: str | None = None


class ParserRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    url: str
    domain: str
    status: str
    note: str | None
    candidate_slug: str | None
    created_at: datetime
    processed_at: datetime | None = None


class GenerateFetcherRequest(BaseModel):
    use_llm: bool = False


class CandidateDetail(BaseModel):
    domain: str
    slug: str
    mode: str | None
    reasoning: str
    content_selectors: tuple[str, ...]
    noise_selectors: tuple[str, ...]
    sample_urls: list[str]
    generated_at: str
    iteration: int
    before_chars: dict[str, int]
    after_chars: dict[str, int]


class GeneratedCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feed_id: int
    domain: str
    slug: str
    status: str
    mode: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None = None
    candidate: CandidateDetail | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class PreferencesUpdate(BaseModel):
    preferences: dict


class PreferencesResponse(BaseModel):
    preferences: dict
    updated_at: Optional[datetime] = None


class GoogleTokenRequest(BaseModel):
    code: str
    redirect_uri: str


# ── Category schemas ──────────────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class CategoryUpdate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    feed_count: int = 0


# ── Feed schemas ─────────────────────────────────────────────────────────────

class FeedCreate(BaseModel):
    url: str
    title: str | None = None
    category_ids: list[int] = []

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url must not be empty")
        return v.strip()


VALID_IMPORTANCE_TIERS = {"must_read", "casual", "archive_only"}
VALID_OPEN_ACTIONS = {"reader", "original", "list"}


class FeedUpdate(BaseModel):
    title: str | None = None
    is_active: bool | None = None
    category_ids: list[int] | None = None
    auto_mark_read: bool | None = None
    default_open_action: str | None = None
    importance_tier: str | None = None
    manual_refresh_only: bool | None = None
    note: str | None = None
    color: str | None = None
    icon_url: str | None = None
    pinned: bool | None = None
    auto_full_content: bool | None = None
    suppress_duplicates: bool | None = None
    refresh_interval_minutes: int | None = None
    retention_days: int | None = None
    max_articles_retained: int | None = None
    webhook_eligible: bool | None = None

    @field_validator("refresh_interval_minutes")
    @classmethod
    def valid_refresh_interval(cls, v: int | None) -> int | None:
        # 0 is accepted as a "clear the override, use the global default" sentinel.
        if v is not None and v != 0 and not (15 <= v <= 10080):
            raise ValueError("refresh_interval_minutes must be 0 (clear override) or between 15 and 10080 (1 week)")
        return v

    @field_validator("retention_days")
    @classmethod
    def valid_retention_days(cls, v: int | None) -> int | None:
        # 0 is accepted as a "clear the retention window, keep indefinitely" sentinel.
        if v is not None and v != 0 and not (1 <= v <= 3650):
            raise ValueError("retention_days must be 0 (clear override) or between 1 and 3650 (10 years)")
        return v

    @field_validator("max_articles_retained")
    @classmethod
    def valid_max_articles_retained(cls, v: int | None) -> int | None:
        # 0 is accepted as a "clear the cap, keep everything" sentinel.
        if v is not None and v != 0 and not (10 <= v <= 100000):
            raise ValueError("max_articles_retained must be 0 (clear cap) or between 10 and 100000")
        return v

    @field_validator("note")
    @classmethod
    def note_max_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 500:
            raise ValueError("note must be 500 characters or fewer")
        return v

    @field_validator("default_open_action")
    @classmethod
    def valid_open_action(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_OPEN_ACTIONS:
            raise ValueError(f"default_open_action must be one of {sorted(VALID_OPEN_ACTIONS)}")
        return v

    @field_validator("importance_tier")
    @classmethod
    def valid_importance_tier(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_IMPORTANCE_TIERS:
            raise ValueError(f"importance_tier must be one of {sorted(VALID_IMPORTANCE_TIERS)}")
        return v

    @field_validator("color")
    @classmethod
    def valid_color(cls, v: str | None) -> str | None:
        if v and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("color must be a 6-digit hex code like #3b82f6")
        return v


class FeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    title: str | None
    description: str | None
    site_url: str | None
    icon_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_fetched_at: datetime | None
    fetch_failure_count: int = 0
    last_success_at: Optional[datetime] = None
    health_snooze_until: Optional[datetime] = None
    articles_per_day_avg: Optional[float] = None
    health_status: str = "healthy"
    article_count: int = 0
    unread_count: int = 0
    categories: list[CategoryResponse] = []
    plugin_name: str | None = None
    auto_mark_read: bool = False
    default_open_action: str = "reader"
    importance_tier: str = "casual"
    manual_refresh_only: bool = False
    note: str | None = None
    color: str | None = None
    pinned: bool = False
    auto_full_content: bool = True
    suppress_duplicates: bool = False
    refresh_interval_minutes: int | None = None
    retention_days: int | None = None
    max_articles_retained: int | None = None
    webhook_eligible: bool = True
    # Computed: True when the feed has unread articles nobody has read in 30+ days
    suggest_unsubscribe: bool = False

    @model_validator(mode="after")
    def compute_health_status(self) -> "FeedResponse":
        now = datetime.now(timezone.utc)
        if self.health_snooze_until and self.health_snooze_until > now:
            self.health_status = "healthy"
            return self
        if self.fetch_failure_count >= 3:
            self.health_status = "failing"
        elif (
            self.last_success_at is None
            or self.last_success_at < now - timedelta(days=30)
        ):
            self.health_status = "stale"
        elif (
            self.articles_per_day_avg is not None
            and self.articles_per_day_avg > 50
        ):
            self.health_status = "noisy"
        else:
            self.health_status = "healthy"
        return self


class FeedSnoozeRequest(BaseModel):
    days: int = 30


class FeedListResponse(BaseModel):
    total: int
    items: list[FeedResponse]


# ── Article schemas ───────────────────────────────────────────────────────────

class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feed_id: int
    # guid omitted — internal deduplication key, never rendered in the UI
    title: str | None
    url: str | None
    author: str | None
    summary: str | None
    content: str | None
    full_content: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    is_read: bool
    is_bookmarked: bool
    tags: list[str] = []
    media_type: str | None = None
    media_url: str | None = None
    duration_seconds: int | None = None
    resume_at_seconds: int | None = None
    episode_number: str | None = None
    itunes_author: str | None = None
    scroll_pct: int | None = None
    story_cluster_id: str | None = None
    article_note: str | None = None
    created_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return []


class ArticleListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ArticleResponse]


class ArticleReadUpdate(BaseModel):
    is_read: bool


class ArticleBookmarkUpdate(BaseModel):
    is_bookmarked: bool


class ArticleTagsUpdate(BaseModel):
    tags: list[str]


class ArticleResumeUpdate(BaseModel):
    resume_at_seconds: int | None


class ArticleScrollUpdate(BaseModel):
    scroll_pct: int


class UserTagsResponse(BaseModel):
    tags: list[str]


# ── Bulk article tag operations ───────────────────────────────────────────────

class BulkTagRequest(BaseModel):
    article_ids: list[int]
    value: bool = True


class BulkMarkReadRequest(BaseModel):
    article_ids: list[int]
    is_read: bool


class BulkBookmarkRequest(BaseModel):
    article_ids: list[int]
    is_bookmarked: bool


class BulkActionResponse(BaseModel):
    updated: int


class BulkSaveLaterResponse(BaseModel):
    updated: int
    fetched: int


# ── Highlight schemas ─────────────────────────────────────────────────────────

class HighlightCreate(BaseModel):
    start_pos: int
    end_pos: int
    color_id: int = 1
    text: Optional[str] = None
    note: Optional[str] = None

    @field_validator("end_pos")
    @classmethod
    def end_after_start(cls, v: int, info) -> int:
        if v <= info.data.get("start_pos", 0):
            raise ValueError("end_pos must be greater than start_pos")
        return v

    @field_validator("color_id")
    @classmethod
    def valid_color(cls, v: int) -> int:
        if v not in (1, 2, 3, 4):
            raise ValueError("color_id must be 1, 2, 3, or 4")
        return v


class HighlightUpdate(BaseModel):
    color_id: Optional[int] = None
    note: Optional[str] = None

    @field_validator("color_id")
    @classmethod
    def valid_color(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (1, 2, 3, 4):
            raise ValueError("color_id must be 1, 2, 3, or 4")
        return v


class HighlightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    start_pos: int
    end_pos: int
    color_id: int
    text: Optional[str] = None
    note: Optional[str] = None
    ai_question: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class HighlightReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    article_title: str | None = None
    article_url: str | None = None
    start_pos: int
    end_pos: int
    color_id: int
    text: Optional[str] = None
    note: Optional[str] = None
    ai_question: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


# ── Search schemas ───────────────────────────────────────────────────────────

class FeedSearchResult(BaseModel):
    feed_url: str
    title: str | None
    description: str | None
    website_url: str | None
    subscribers: int | None
    language: str | None
    cover_url: str | None
    velocity: float | None


class FeedSearchResponse(BaseModel):
    query: str
    results: list[FeedSearchResult]
    related_queries: list[str] = []


class DiscoveredFeed(BaseModel):
    feed_url: str
    title: str | None = None
    feed_type: str | None = None


class FeedDiscoverResponse(BaseModel):
    source_url: str
    feeds: list[DiscoveredFeed]


# ── OPML schemas ──────────────────────────────────────────────────────────────

class OPMLImportResult(BaseModel):
    added: int
    skipped: int
    failed: int
    errors: list[str] = []


# ── Refresh result ────────────────────────────────────────────────────────────

class RefreshResult(BaseModel):
    feed_id: int
    new_articles: int
    message: str


# ── Payments (Razorpay) ───────────────────────────────────────────────────────

class PaymentOrderCreate(BaseModel):
    plan: str  # purchasable plan id, e.g. "paid"


class PaymentOrderResponse(BaseModel):
    order_id: str
    amount: int   # smallest currency unit (paise for INR) — pass straight to Checkout.js
    currency: str
    key_id: str   # Razorpay key ID — public, safe to expose to the client
    plan: str


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ── Reading stats ─────────────────────────────────────────────────────────────

class DailyReadCount(BaseModel):
    date: str
    count: int


class TopFeedStat(BaseModel):
    feed_id: int
    title: str | None
    read_count: int


class ReadingStatsResponse(BaseModel):
    total_articles: int
    total_read: int
    total_unread: int
    total_bookmarked: int
    read_today: int
    read_this_week: int
    current_streak: int
    longest_streak: int
    daily_counts: list[DailyReadCount]
    top_feeds: list[TopFeedStat]


# ── Collections (curated, shareable feed lists) ──────────────────────────────

class CollectionItemCreate(BaseModel):
    feed_url: str
    title: str | None = None
    icon_url: str | None = None

    @field_validator("feed_url")
    @classmethod
    def validate_feed_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("feed_url is required")
        return v


class CollectionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feed_url: str
    title: str | None
    icon_url: str | None
    position: int


class CollectionCreate(BaseModel):
    name: str
    description: str | None = None
    is_public: bool = False
    items: list[CollectionItemCreate] = []

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v


class CollectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_public: bool | None = None


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    owner_name: str | None = None
    name: str
    slug: str
    description: str | None
    is_public: bool
    subscriber_count: int
    is_subscribed: bool = False
    is_owner: bool = False
    items: list[CollectionItemResponse] = []
    created_at: datetime
    updated_at: datetime


class CollectionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CollectionResponse]


class CollectionSubscribeResult(BaseModel):
    subscribed: bool
    feeds_added: int


# ── Webhooks ──────────────────────────────────────────────────────────────────

WEBHOOK_EVENTS = {"new_article", "highlight_created", "alert_matched"}


class WebhookCreate(BaseModel):
    url: str
    events: list[str] = ["new_article"]
    secret: str | None = None

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        return v

    @field_validator("events")
    @classmethod
    def valid_events(cls, v: list[str]) -> list[str]:
        invalid = set(v) - WEBHOOK_EVENTS
        if invalid:
            raise ValueError(f"Unknown event types: {invalid}")
        return v


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    last_fired_at: Optional[datetime] = None

    @field_validator("events", mode="before")
    @classmethod
    def parse_events(cls, v) -> list[str]:
        if isinstance(v, str):
            import json as _json
            try:
                return _json.loads(v)
            except Exception:
                return []
        return v or []


# ── Article routing rule schemas ──────────────────────────────────────────────

RULE_STRING_FIELDS = {"title", "author", "content"}
RULE_NUM_FIELDS = {"feed_id", "read_time_min"}
RULE_FIELDS = RULE_STRING_FIELDS | RULE_NUM_FIELDS
RULE_STRING_OPS = {"contains", "not_contains"}
RULE_NUM_OPS = {"eq", "neq", "gt", "lt"}
RULE_ACTION_TYPES = {"add_tag", "mark_read", "bookmark", "read_later"}

# feed_id is a label, not an ordered numeric value — restrict to identity checks only
RULE_FIELD_OPS: dict[str, set[str]] = {
    "title": RULE_STRING_OPS,
    "author": RULE_STRING_OPS,
    "content": RULE_STRING_OPS,
    "feed_id": {"eq", "neq"},
    "read_time_min": RULE_NUM_OPS,
}


class RuleCondition(BaseModel):
    field: str
    op: str
    value: str | int | float

    @model_validator(mode="after")
    def validate_condition(self) -> "RuleCondition":
        if self.field not in RULE_FIELDS:
            raise ValueError(f"Unknown field '{self.field}'. Valid: {sorted(RULE_FIELDS)}")
        allowed_ops = RULE_FIELD_OPS[self.field]
        if self.op not in allowed_ops:
            raise ValueError(f"Op '{self.op}' not valid for field '{self.field}'")
        return self


class RuleAction(BaseModel):
    type: str
    value: Optional[str] = None

    @model_validator(mode="after")
    def validate_action(self) -> "RuleAction":
        if self.type not in RULE_ACTION_TYPES:
            raise ValueError(f"Unknown action type '{self.type}'. Valid: {sorted(RULE_ACTION_TYPES)}")
        if self.type == "add_tag" and not (self.value or "").strip():
            raise ValueError("add_tag requires a non-empty value")
        return self


class RuleCreate(BaseModel):
    name: str
    conditions: list[RuleCondition]
    actions: list[RuleAction]
    is_active: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    conditions: Optional[list[RuleCondition]] = None
    actions: Optional[list[RuleAction]] = None


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    conditions: list[dict]
    actions: list[dict]
    match_count: int
    order: int
    created_at: datetime


# ── Feature vote schemas ──────────────────────────────────────────────────────

VALID_FEATURE_KEYS = frozenset({
    "highlight_notes",
    "search_alerts",
    "email_digest",
    "readwise_sync",
    "personal_api_token",
})


class FeatureVoteCreate(BaseModel):
    feature_key: str

    @field_validator("feature_key")
    @classmethod
    def valid_feature_key(cls, v: str) -> str:
        if v not in VALID_FEATURE_KEYS:
            raise ValueError("unknown feature_key")
        return v


class FeatureVoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feature_key: str
    created_at: datetime
