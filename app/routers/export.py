import io
import json
import re
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, nullslast, or_
from sqlalchemy.orm import Session

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
    articles = (
        db.query(Article)
        .join(Feed, Article.feed_id == Feed.id)
        .filter(
            Feed.user_id == current_user.id,
            or_(
                Article.article_note.isnot(None),
                has_highlight,
            ),
        )
        .order_by(nullslast(Article.published_at.desc()))
        .all()
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for article in articles:
            feed = article.feed
            highlights = (
                db.query(Highlight)
                .filter(
                    Highlight.article_id == article.id,
                    Highlight.user_id == current_user.id,
                )
                .order_by(Highlight.start_pos)
                .all()
            )
            md = _build_md(article, feed, highlights)
            slug = _slugify(article.title or "")
            zf.writestr(f"{article.id}-{slug}.md", md)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=knowledge-vault.zip"},
    )
