import io
import json
import re
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, nullslast, or_
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import Article, Feed, Highlight, User

router = APIRouter(prefix="/export", tags=["Export"])


def _slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s[:max_len] or "untitled"


_SYSTEM_TAGS = {"saved_later", "read_later", "read", "unread"}


def _parse_tags(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        tags = raw
    else:
        try:
            parsed = json.loads(raw)
            tags = parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return [t for t in tags if t not in _SYSTEM_TAGS]


def _build_md(article: Article, feed: Feed | None, highlights: list[Highlight]) -> str:
    lines: list[str] = []

    # YAML frontmatter — double-quoted strings break on literal newlines and backslashes
    def safe(s: str | None) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', "'").replace("\n", " ").replace("\r", "")
    lines.append("---")
    lines.append(f'title: "{safe(article.title or "Untitled")}"')
    if article.url:
        lines.append(f'url: "{safe(article.url)}"')
    if feed:
        lines.append(f'feed: "{safe(feed.title or feed.url)}"')
    if article.published_at:
        lines.append(f'published: "{article.published_at.strftime("%Y-%m-%d")}"')
    tags = _parse_tags(article.tags)
    if tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f'  - "{safe(t)}"')
    lines.append("---")
    lines.append("")

    # Article-level note
    if article.article_note and article.article_note.strip():
        lines.append("## Notes")
        lines.append("")
        lines.append(article.article_note.strip())
        lines.append("")

    # Highlights
    if highlights:
        lines.append("## Highlights")
        lines.append("")
        for h in highlights:
            if h.text:
                lines.append("> " + h.text.replace("\n", "\n> "))
            else:
                lines.append(f"> [chars {h.start_pos}–{h.end_pos}]")
            if h.note and h.note.strip():
                lines.append("")
                lines.append(h.note.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


_EXPORT_CHUNK = 100   # articles processed per DB round-trip during export


@router.get(
    "/markdown",
    summary="Export knowledge vault as Markdown ZIP",
    description=(
        "Returns a ZIP archive — one .md file per article that has at least one "
        "highlight or an article note. Each file has YAML frontmatter "
        "(title, url, feed, published, tags) followed by the article note and "
        "highlights with inline user notes."
    ),
)
def export_markdown_vault(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    has_highlight = exists().where(
        Highlight.article_id == Article.id,
        Highlight.user_id == current_user.id,
    )

    # Collect only the IDs first — avoids loading all article content at once.
    article_ids: list[int] = [
        row[0]
        for row in (
            db.query(Article.id)
            .join(Feed, Article.feed_id == Feed.id)
            .filter(
                Feed.user_id == current_user.id,
                or_(Article.article_note.isnot(None), has_highlight),
            )
            .order_by(nullslast(Article.published_at.desc()))
            .all()
        )
    ]

    # Pre-fetch all relevant highlights in one query, keyed by article_id.
    highlight_map: dict[int, list[Highlight]] = {}
    for i in range(0, len(article_ids), _EXPORT_CHUNK):
        chunk_ids = article_ids[i : i + _EXPORT_CHUNK]
        rows = (
            db.query(Highlight)
            .filter(
                Highlight.article_id.in_(chunk_ids),
                Highlight.user_id == current_user.id,
            )
            .order_by(Highlight.article_id, Highlight.start_pos)
            .all()
        )
        for h in rows:
            highlight_map.setdefault(h.article_id, []).append(h)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Process articles in chunks so we never hold the full set in memory.
        for i in range(0, len(article_ids), _EXPORT_CHUNK):
            chunk_ids = article_ids[i : i + _EXPORT_CHUNK]
            articles = (
                db.query(Article)
                .filter(Article.id.in_(chunk_ids))
                .options(selectinload(Article.feed))
                .all()
            )
            # Re-sort to match original ordering (IN loses order).
            id_order = {aid: idx for idx, aid in enumerate(chunk_ids)}
            articles.sort(key=lambda a: id_order.get(a.id, 0))

            for article in articles:
                highlights = highlight_map.get(article.id, [])
                md = _build_md(article, article.feed, highlights)
                slug = _slugify(article.title or "")
                zf.writestr(f"{article.id}-{slug}.md", md)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=knowledge-vault.zip"},
    )
