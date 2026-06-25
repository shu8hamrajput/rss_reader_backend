from datetime import datetime, timedelta, timezone as _tz
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, load_only

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Article, Feed, User

router = APIRouter(tags=["Briefings"])

_TIMEFRAME_LABELS = {
    'today': 'Today',
    'last24h': 'Last 24 hours',
    'thisweek': 'This week',
    'last7days': 'Last 7 days',
}

_TEMPLATE_LABELS = {
    'general': 'General Digest',
    'research': 'Research Digest',
    'industry': 'Industry Briefing',
    'policy': 'Policy Monitor',
}

_SYSTEM_PROMPTS = {
    'general': (
        "You create concise briefings from RSS article summaries. "
        "Produce a well-structured Markdown document. Group articles by theme. "
        "Highlight the most important developments. Be direct and clear. "
        "Use ## headers for sections, bullet points for items."
    ),
    'research': (
        "You are an academic research assistant. Synthesize these articles into a structured literature digest. "
        "Identify key themes, notable findings, and open questions worth investigating. "
        "Use academic but accessible language. Format as Markdown with sections: "
        "## Key Themes, ## Notable Findings, ## Open Questions, ## Suggested Reading."
    ),
    'industry': (
        "You are a strategic intelligence analyst. Create a concise executive briefing. "
        "Start with a 2-3 sentence ## Executive Summary. "
        "Then group key developments by theme under ## headings. "
        "End with ## What to Watch (3-5 forward-looking bullet points). "
        "Format as Markdown. Be direct — the audience is busy executives."
    ),
    'policy': (
        "You are a policy analyst. Create a formal policy monitoring report. "
        "Sections: ## Summary, ## Key Developments, ## Stakeholder Positions, ## Implications, ## Sources. "
        "Cite articles by title in square brackets e.g. [Article Title]. "
        "Use formal but clear language. Format as Markdown."
    ),
}


class BriefingRequest(BaseModel):
    feed_ids: list[int] = []
    category_id: int | None = None
    timeframe: Literal['today', 'last24h', 'thisweek', 'last7days'] = 'last7days'
    template: Literal['general', 'research', 'industry', 'policy'] = 'general'


def _cutoff(timeframe: str) -> datetime:
    now = datetime.now(_tz.utc)
    if timeframe == 'today':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == 'last24h':
        return now - timedelta(hours=24)
    if timeframe == 'thisweek':
        return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=7)  # last7days


@router.post("/briefings/generate", summary="Stream an AI briefing from recent articles")
async def generate_briefing(
    payload: BriefingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="AI briefing not configured — set ANTHROPIC_API_KEY")

    cutoff = _cutoff(payload.timeframe)
    q = (
        db.query(Article, Feed)
        .join(Feed, Article.feed_id == Feed.id)
        .filter(Feed.user_id == current_user.id)
        .filter(Article.published_at >= cutoff)
    )
    if payload.feed_ids:
        q = q.filter(Article.feed_id.in_(payload.feed_ids))
    elif payload.category_id is not None:
        q = q.filter(Feed.categories.any(id=payload.category_id))

    # Only load the columns the briefing prompt actually uses.
    # full_content (fetched HTML, up to MBs) and search_vector (binary tsvector)
    # are never read here — skipping them cuts per-briefing DB transfer dramatically.
    rows = (
        q.options(
            load_only(
                Article.id, Article.feed_id, Article.title,
                Article.summary, Article.content, Article.published_at,
            ),
            load_only(Feed.id, Feed.title, Feed.url),
        )
        .order_by(Article.published_at.desc())
        .limit(30)
        .all()
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No articles found for the selected sources in the '{_TIMEFRAME_LABELS[payload.timeframe]}' window",
        )

    article_blocks = []
    source_names: set[str] = set()
    for i, (article, feed) in enumerate(rows, 1):
        title = article.title or "Untitled"
        source = feed.title or feed.url
        source_names.add(source)
        pub = article.published_at.strftime("%Y-%m-%d") if article.published_at else "n/d"
        raw = (article.summary or article.content or "").strip()
        snippet = raw[:400] + "…" if len(raw) > 400 else raw
        article_blocks.append(f'[{i}] "{title}"\nSource: {source} | {pub}\n{snippet}')

    sorted_sources = sorted(source_names)
    source_label = ", ".join(sorted_sources[:3])
    if len(sorted_sources) > 3:
        source_label += f" (+{len(sorted_sources) - 3} more)"

    user_message = (
        f"Time period: {_TIMEFRAME_LABELS[payload.timeframe]}\n"
        f"Sources: {source_label}\n"
        f"Articles ({len(rows)} total):\n\n"
        + "\n\n".join(article_blocks)
        + f"\n\nGenerate a {_TEMPLATE_LABELS[payload.template]} for this content."
    )

    from anthropic import Anthropic
    client = Anthropic(api_key=settings.anthropic_api_key)

    def _stream():
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_SYSTEM_PROMPTS[payload.template],
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")
