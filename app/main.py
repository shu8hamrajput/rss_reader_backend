from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from .config import settings
from .database import Base, engine
from .routers import articles, auth, categories, collections, feeds, highlights, opml, payments, preferences, search, stream

# ── Rate limiter (IP-based; Redis-backed so limits are shared across workers) ─
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
    storage_uri=settings.redis_url,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate()
    yield


def _migrate() -> None:
    """Additive schema setup beyond what create_all handles (indexes, triggers)."""
    with engine.connect() as conn:
        stmts = [
            # Subscription plan — gates feed-count / full-content-fetch limits
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(32) NOT NULL DEFAULT 'free'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ",
            # Synced client preferences (theme, layout, default view, reader font, saved searches)
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences JSONB",
            # Reading stats: timestamp of when an article was marked read
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ",
            # Podcast / rich-media enclosure fields
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS media_type VARCHAR(100)",
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS media_url TEXT",
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS duration_seconds INTEGER",
            # Full-text search: keep articles.search_vector in sync via trigger
            """CREATE OR REPLACE FUNCTION articles_search_vector_update() RETURNS trigger AS $$
               BEGIN
                 NEW.search_vector := to_tsvector('english',
                   coalesce(NEW.title, '') || ' ' || coalesce(NEW.summary, '') || ' ' || coalesce(NEW.content, ''));
                 RETURN NEW;
               END
               $$ LANGUAGE plpgsql""",
            "DROP TRIGGER IF EXISTS articles_search_vector_trigger ON articles",
            """CREATE TRIGGER articles_search_vector_trigger
               BEFORE INSERT OR UPDATE OF title, summary, content ON articles
               FOR EACH ROW EXECUTE FUNCTION articles_search_vector_update()""",
            "CREATE INDEX IF NOT EXISTS ix_articles_search_vector ON articles USING GIN (search_vector)",
            # Feed health tracking
            "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS fetch_failure_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ",
            # Backfill search_vector for rows written before the trigger existed
            """UPDATE articles SET search_vector = to_tsvector('english',
                 coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(content, ''))
               WHERE search_vector IS NULL""",
            # Per-user preferences table for cross-device settings sync
            """CREATE TABLE IF NOT EXISTS user_preferences (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                 preferences JSONB NOT NULL DEFAULT '{}',
                 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )""",
            # JWT revocation: bump token_version to invalidate all tokens for a user
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
        ]
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()


app = FastAPI(
    title="RSS Reader API",
    description=(
        "A RESTful backend for managing RSS feed subscriptions and articles.\n\n"
        "## Features\n"
        "- **Auth** — Google OAuth2 login; JWT Bearer tokens on all protected routes\n"
        "- **Feeds** — subscribe, update, refresh, and unsubscribe; ETag/Last-Modified caching\n"
        "- **Categories** — organise feeds into folders; many-to-many relationship\n"
        "- **Articles** — list, paginate, full-text search (PostgreSQL tsvector), read/unread, bookmark\n"
        "- **OPML** — bulk import from file; export all feeds as standard OPML\n"
        "- **Auto-refresh** — Celery beat dispatches a refresh of all active feeds every 30 minutes\n"
        "- **SSE** — live new-article stream over Server-Sent Events, backed by Redis pub/sub\n"
        "- **Search** — query the Feedly public index or discover feeds on any website\n"
        "- **Plans & billing** — free / paid tiers gating feed count and full-content "
        "fetches; upgrades via Razorpay Checkout (orders, signature verification, webhooks)\n"
        "- **Rate limiting** — 200 req/min per IP (Redis-backed); refresh endpoint capped at 10/min\n\n"
        "## Authentication\n"
        "All `/feeds`, `/articles`, `/categories`, and `/opml` routes require "
        "`Authorization: Bearer <token>`.\n\n"
        "1. `GET /api/v1/auth/google` → redirects to Google consent\n"
        "2. Google redirects back → `TokenResponse` with JWT\n"
        "3. Mobile/SPA: `POST /api/v1/auth/google/token` with `{code, redirect_uri}`"
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,       prefix="/api/v1")
app.include_router(feeds.router,      prefix="/api/v1")
app.include_router(articles.router,   prefix="/api/v1")
app.include_router(highlights.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(opml.router,       prefix="/api/v1")
app.include_router(payments.router,   prefix="/api/v1")
app.include_router(stream.router,     prefix="/api/v1")
app.include_router(search.router,     prefix="/api/v1")
app.include_router(collections.router,  prefix="/api/v1")
app.include_router(preferences.router,  prefix="/api/v1")


@app.get("/health", tags=["Health"], summary="Health check")
def health():
    return {"status": "ok"}
