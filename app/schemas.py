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
    created_at: datetime
    last_login_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


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
