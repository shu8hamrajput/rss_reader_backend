import logging
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
from .routers import alerts, articles, auth, briefings, categories, collections, export, feature_votes, feeds, fetchers, highlights, opml, payments, preferences, rules, search, stream, webhooks

# ── Rate limiter (IP-based; Redis-backed so limits are shared across workers) ─
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
    storage_uri=settings.redis_url,
)
logger = logging.getLogger(__name__)


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
            # Backfill search_vector only when there are rows that need it.
            # Wrapping in a PL/pgSQL DO block with an EXISTS check avoids a full
            # table scan on every cold start once all rows are already populated.
            """DO $$
               BEGIN
                 IF EXISTS (SELECT 1 FROM articles WHERE search_vector IS NULL LIMIT 1) THEN
                   UPDATE articles
                   SET    search_vector = to_tsvector('english',
                            coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(content, ''))
                   WHERE  search_vector IS NULL;
                 END IF;
               END $$""",
            # Per-user preferences table for cross-device settings sync
            """CREATE TABLE IF NOT EXISTS user_preferences (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                 preferences JSONB NOT NULL DEFAULT '{}',
                 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )""",
            # JWT revocation: bump token_version to invalidate all tokens for a user
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
            # Personal API token for scripting / third-party integrations
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS api_token VARCHAR(64) UNIQUE",
            # Saved-search alerts: notify user when new articles match a stored query
            """CREATE TABLE IF NOT EXISTS search_alerts (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 query VARCHAR(512) NOT NULL,
                 label VARCHAR(256),
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 last_matched_at TIMESTAMPTZ,
                 UNIQUE(user_id, query)
               )""",
            "CREATE INDEX IF NOT EXISTS ix_search_alerts_user_id ON search_alerts (user_id)",
            # Alert match history — articles that triggered a search alert, for digests
            """CREATE TABLE IF NOT EXISTS alert_matches (
                 id SERIAL PRIMARY KEY,
                 alert_id INTEGER NOT NULL REFERENCES search_alerts(id) ON DELETE CASCADE,
                 feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                 article_ids TEXT NOT NULL DEFAULT '[]',
                 count INTEGER NOT NULL DEFAULT 0,
                 matched_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )""",
            "CREATE INDEX IF NOT EXISTS ix_alert_matches_alert_id ON alert_matches (alert_id)",
            # User-requested parser improvements — picked up by `make process-parser-requests`
            """CREATE TABLE IF NOT EXISTS parser_requests (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                 url VARCHAR(2048) NOT NULL,
                 domain VARCHAR(256) NOT NULL,
                 status VARCHAR(16) NOT NULL DEFAULT 'pending',
                 note TEXT,
                 candidate_slug VARCHAR(256),
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 processed_at TIMESTAMPTZ
               )""",
            "CREATE INDEX IF NOT EXISTS ix_parser_requests_domain ON parser_requests (domain)",
            "CREATE INDEX IF NOT EXISTS ix_parser_requests_status ON parser_requests (status)",
            # Podcast playback position — stores where the user left off for cross-device resume
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS resume_at_seconds INTEGER",
            # iTunes / podcast metadata fields
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS episode_number VARCHAR(32)",
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS itunes_author VARCHAR(256)",
            # Feed health snooze — suppresses failing/stale status until this timestamp
            "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS health_snooze_until TIMESTAMPTZ",
            # User login sessions — device tracking
            """CREATE TABLE IF NOT EXISTS user_sessions (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 device_info VARCHAR(512),
                 ip_address VARCHAR(64),
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )""",
            "CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions (user_id)",
            # Reading depth (scroll position) for cross-device text article resume
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS scroll_pct SMALLINT",
            # Story clustering — articles covering the same event share a UUID
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS story_cluster_id VARCHAR(36)",
            "CREATE INDEX IF NOT EXISTS ix_articles_story_cluster ON articles (story_cluster_id) WHERE story_cluster_id IS NOT NULL",
            # Spaced repetition for highlights
            "ALTER TABLE highlights ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
            # Feed velocity — rolling 7-day average articles/day for anomaly detection
            "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS articles_per_day_avg FLOAT",
            # User-configurable webhooks for integrations (Readwise, Obsidian, Zapier)
            """CREATE TABLE IF NOT EXISTS user_webhooks (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 url VARCHAR(2048) NOT NULL,
                 events TEXT NOT NULL DEFAULT '["new_article"]',
                 secret VARCHAR(256),
                 is_active BOOLEAN NOT NULL DEFAULT TRUE,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 last_fired_at TIMESTAMPTZ
               )""",
            "CREATE INDEX IF NOT EXISTS ix_user_webhooks_user_id ON user_webhooks (user_id)",
            # Article routing rules
            """CREATE TABLE IF NOT EXISTS article_rules (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 name VARCHAR(200) NOT NULL,
                 is_active BOOLEAN NOT NULL DEFAULT TRUE,
                 conditions JSON NOT NULL DEFAULT '[]',
                 actions JSON NOT NULL DEFAULT '[]',
                 match_count INTEGER NOT NULL DEFAULT 0,
                 "order" INTEGER NOT NULL DEFAULT 0,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )""",
            "CREATE INDEX IF NOT EXISTS ix_article_rules_user_id ON article_rules (user_id)",
            # Highlight annotation — captured text + user note
            "ALTER TABLE highlights ADD COLUMN IF NOT EXISTS text TEXT",
            "ALTER TABLE highlights ADD COLUMN IF NOT EXISTS note TEXT",
            # Article-level note — user's freeform annotation for the whole article
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS article_note TEXT",
            # Feature votes — roadmap voting from the logged-out LoginPage
            """CREATE TABLE IF NOT EXISTS feature_votes (
                 id SERIAL PRIMARY KEY,
                 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 feature_key VARCHAR(64) NOT NULL,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 UNIQUE(user_id, feature_key)
               )""",
            "CREATE INDEX IF NOT EXISTS ix_feature_votes_user_id ON feature_votes (user_id)",
            # Self-healing feeds — parser_gen fetcher candidates generated from Feed Health
            """CREATE TABLE IF NOT EXISTS generated_candidates (
                 id SERIAL PRIMARY KEY,
                 feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                 domain VARCHAR(256) NOT NULL,
                 slug VARCHAR(256) NOT NULL,
                 status VARCHAR(16) NOT NULL DEFAULT 'pending',
                 mode VARCHAR(16),
                 error TEXT,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 completed_at TIMESTAMPTZ
               )""",
            "CREATE INDEX IF NOT EXISTS ix_generated_candidates_feed_id ON generated_candidates (feed_id)",
            "CREATE INDEX IF NOT EXISTS ix_generated_candidates_domain ON generated_candidates (domain)",
            "CREATE INDEX IF NOT EXISTS ix_generated_candidates_status ON generated_candidates (status)",
            # AI-generated Anki question for highlights
            "ALTER TABLE highlights ADD COLUMN IF NOT EXISTS ai_question TEXT",
            # Performance indexes for common article filter/sort columns
            "CREATE INDEX IF NOT EXISTS ix_articles_is_read ON articles (is_read)",
            "CREATE INDEX IF NOT EXISTS ix_articles_is_bookmarked ON articles (is_bookmarked)",
            "CREATE INDEX IF NOT EXISTS ix_articles_published_at ON articles (published_at DESC NULLS LAST)",
            "CREATE INDEX IF NOT EXISTS ix_articles_read_at ON articles (read_at)",
            "CREATE INDEX IF NOT EXISTS ix_articles_created_at ON articles (created_at)",
        ]
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as exc:
                logger.warning("Migration statement failed (non-fatal): %s — %s", stmt[:120], exc)
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
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
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
app.include_router(alerts.router,       prefix="/api/v1")
app.include_router(webhooks.router,     prefix="/api/v1")
app.include_router(rules.router,        prefix="/api/v1")
app.include_router(export.router,       prefix="/api/v1")
app.include_router(feature_votes.router, prefix="/api/v1")
app.include_router(fetchers.router,      prefix="/api/v1")
app.include_router(briefings.router,     prefix="/api/v1")


@app.get("/health", tags=["Health"], summary="Health check")
def health():
    return {"status": "ok"}
