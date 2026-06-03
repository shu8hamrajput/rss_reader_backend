from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from .database import Base, engine
from .routers import articles, auth, categories, feeds, highlights, opml, search, stream
from .services import scheduler as sched

# ── Rate limiter (IP-based; swap key_func for user-based if needed) ───────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate()
    sched.start()
    yield
    sched.stop()


def _migrate() -> None:
    """Additive schema migrations for databases created before this version."""
    with engine.connect() as conn:
        stmts = [
            # Pre-auth schema
            "ALTER TABLE feeds ADD COLUMN user_id INTEGER REFERENCES users(id)",
            # ETag / Last-Modified caching
            "ALTER TABLE feeds ADD COLUMN etag TEXT",
            "ALTER TABLE feeds ADD COLUMN last_modified TEXT",
            # FTS5 virtual table (full-text search)
            """CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts
               USING fts5(title, summary, content, content='articles', content_rowid='id')""",
            # FTS5 sync triggers
            """CREATE TRIGGER IF NOT EXISTS articles_fts_insert
               AFTER INSERT ON articles BEGIN
                 INSERT INTO articles_fts(rowid, title, summary, content)
                 VALUES (new.id, new.title, new.summary, new.content);
               END""",
            """CREATE TRIGGER IF NOT EXISTS articles_fts_delete
               BEFORE DELETE ON articles BEGIN
                 INSERT INTO articles_fts(articles_fts, rowid, title, summary, content)
                 VALUES ('delete', old.id, old.title, old.summary, old.content);
               END""",
            """CREATE TRIGGER IF NOT EXISTS articles_fts_update
               AFTER UPDATE ON articles BEGIN
                 INSERT INTO articles_fts(articles_fts, rowid, title, summary, content)
                 VALUES ('delete', old.id, old.title, old.summary, old.content);
                 INSERT INTO articles_fts(rowid, title, summary, content)
                 VALUES (new.id, new.title, new.summary, new.content);
               END""",
            # Backfill FTS from existing articles
            """INSERT INTO articles_fts(rowid, title, summary, content)
               SELECT id, title, summary, content FROM articles
               WHERE id NOT IN (SELECT rowid FROM articles_fts)""",
            # Tags and full content storage
            "ALTER TABLE articles ADD COLUMN tags TEXT",
            "ALTER TABLE articles ADD COLUMN full_content TEXT",
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
        "- **Articles** — list, paginate, full-text search (SQLite FTS5), read/unread, bookmark\n"
        "- **OPML** — bulk import from file; export all feeds as standard OPML\n"
        "- **Auto-refresh** — background scheduler refreshes all active feeds every 30 minutes\n"
        "- **SSE** — live new-article stream over Server-Sent Events\n"
        "- **Search** — query the Feedly public index or discover feeds on any website\n"
        "- **Rate limiting** — 200 req/min per IP; refresh endpoint capped at 10/min\n\n"
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
app.include_router(stream.router,     prefix="/api/v1")
app.include_router(search.router,     prefix="/api/v1")


@app.get("/health", tags=["Health"], summary="Health check")
def health():
    return {"status": "ok"}
