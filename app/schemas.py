import json
from datetime import datetime
from pydantic import BaseModel, field_validator, ConfigDict


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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class PreferencesUpdate(BaseModel):
    preferences: dict


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


class FeedUpdate(BaseModel):
    title: str | None = None
    is_active: bool | None = None
    category_ids: list[int] | None = None


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
    article_count: int = 0
    unread_count: int = 0
    categories: list[CategoryResponse] = []


class FeedListResponse(BaseModel):
    total: int
    items: list[FeedResponse]


# ── Article schemas ───────────────────────────────────────────────────────────

class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feed_id: int
    guid: str
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
    color_id: int

    @field_validator("color_id")
    @classmethod
    def valid_color(cls, v: int) -> int:
        if v not in (1, 2, 3, 4):
            raise ValueError("color_id must be 1, 2, 3, or 4")
        return v


class HighlightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    start_pos: int
    end_pos: int
    color_id: int
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
    title: str | None
    feed_type: str | None


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
