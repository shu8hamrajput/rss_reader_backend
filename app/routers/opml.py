"""
OPML import and export.

Import: POST /opml/import   (multipart file upload or raw XML body)
Export: GET  /opml/export   (returns application/xml)
"""
import csv
import io
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Annotated

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import Category, Feed, User
from ..schemas import OPMLImportResult
from ..services.feed_parser import refresh_feed

router = APIRouter(prefix="/opml", tags=["OPML"])

logger = logging.getLogger(__name__)

_MAX_OPML_SIZE = 5 * 1024 * 1024  # 5 MB


# ── Import ──────────────────────────────────────────────────────────────────────────────────────

def _iter_outlines(element: ET.Element, folder: str | None = None):
    """Yield (xml_url, title, folder) tuples from nested OPML outlines."""
    for outline in element.findall("outline"):
        xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
        title = outline.get("title") or outline.get("text")
        feed_type = (outline.get("type") or "").lower()

        if xml_url and feed_type in ("rss", "atom", ""):
            yield xml_url.strip(), title, folder
        else:
            # Treat as a folder — recurse
            folder_name = title or outline.get("text")
            yield from _iter_outlines(outline, folder_name)


@router.post(
    "/import",
    response_model=OPMLImportResult,
    summary="Import feeds from an OPML file",
    description=(
        "Upload an OPML file to bulk-subscribe to feeds. "
        "Folders in the OPML become categories. "
        "Already-subscribed feeds are skipped."
    ),
)
async def import_opml(
    file: Annotated[UploadFile, File(description="OPML file (.opml or .xml)")],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw = await file.read(_MAX_OPML_SIZE + 1)
    if len(raw) > _MAX_OPML_SIZE:
        raise HTTPException(status_code=413, detail="OPML file too large (max 5 MB)")
    try:
        root = DefusedET.fromstring(raw.decode("utf-8", errors="replace"))
    except (ET.ParseError, DefusedXmlException) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid XML: {exc}")

    body = root.find("body")
    if body is None:
        raise HTTPException(status_code=422, detail="OPML has no <body> element")

    added = skipped = failed = 0
    errors: list[str] = []
    category_cache: dict[str, Category] = {}

    for xml_url, title, folder_name in _iter_outlines(body):
        # Resolve or create category
        cat: Category | None = None
        if folder_name:
            if folder_name not in category_cache:
                cat = db.query(Category).filter(
                    Category.user_id == current_user.id, Category.name == folder_name
                ).first()
                if not cat:
                    cat = Category(user_id=current_user.id, name=folder_name)
                    db.add(cat)
                    db.flush()
                category_cache[folder_name] = cat
            cat = category_cache[folder_name]

        # Check for existing subscription
        existing = db.query(Feed).filter(
            Feed.url == xml_url, Feed.user_id == current_user.id
        ).first()
        if existing:
            skipped += 1
            continue

        feed = Feed(url=xml_url, title=title, user_id=current_user.id)
        if cat:
            feed.categories.append(cat)
        db.add(feed)
        db.flush()

        try:
            await refresh_feed(feed, db)
            added += 1
        except Exception as exc:
            errors.append(f"{xml_url}: {exc}")
            failed += 1

    db.commit()
    return OPMLImportResult(added=added, skipped=skipped, failed=failed, errors=errors)


# ── Export ────────────────────────────────────────────────────────────────────────────────────

@router.get(
    "/export",
    summary="Export your feeds as an OPML file",
    description="Downloads all your subscribed feeds as a standard OPML 1.0 file, grouped by category.",
    response_class=Response,
)
def export_opml(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feeds = db.query(Feed).filter(Feed.user_id == current_user.id).limit(10_000).all()
    categories = (
        db.query(Category)
        .options(selectinload(Category.feeds))
        .filter(Category.user_id == current_user.id)
        .limit(10_000)
        .all()
    )

    root = ET.Element("opml", version="1.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = f"{current_user.name or current_user.email}'s feeds"
    ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    body = ET.SubElement(root, "body")

    # Categorised feeds
    categorised_feed_ids: set[int] = set()
    for cat in categories:
        if not cat.feeds:
            continue
        folder = ET.SubElement(body, "outline", text=cat.name, title=cat.name)
        for feed in cat.feeds:
            ET.SubElement(
                folder,
                "outline",
                type="rss",
                text=feed.title or feed.url,
                title=feed.title or feed.url,
                xmlUrl=feed.url,
                **{"htmlUrl": feed.site_url} if feed.site_url else {},
            )
            categorised_feed_ids.add(feed.id)

    # Uncategorised feeds
    for feed in feeds:
        if feed.id in categorised_feed_ids:
            continue
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=feed.title or feed.url,
            title=feed.title or feed.url,
            xmlUrl=feed.url,
            **{"htmlUrl": feed.site_url} if feed.site_url else {},
        )

    xml_bytes = ET.tostring(root, encoding="unicode", xml_declaration=False)
    xml_bytes = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": "attachment; filename=feeds.opml"},
    )


# ── YouTube subscriptions CSV import ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/import-youtube",
    response_model=OPMLImportResult,
    summary="Import YouTube subscriptions from Google Takeout CSV",
    description=(
        "Upload the subscriptions.csv file exported from YouTube Studio → "
        "Settings → Advanced settings → Download YouTube data, or Google Takeout → "
        "YouTube → subscriptions.csv. Each channel becomes a YouTube RSS feed subscription. "
        "Columns expected: 'Channel Id', 'Channel Url', 'Channel Title'."
    ),
)
async def import_youtube_csv(
    file: Annotated[UploadFile, File(description="YouTube subscriptions.csv from Google Takeout")],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw = await file.read(5 * 1024 * 1024)
    try:
        text = raw.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded CSV")

    from ..services.plans import effective_plan, limits_for
    plan_limits = limits_for(effective_plan(current_user))
    current_count = db.query(func.count(Feed.id)).filter(Feed.user_id == current_user.id).scalar() or 0

    added = skipped = failed = 0
    errors: list[str] = []

    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {exc}")

    # YouTube Takeout CSV has columns: Channel Id, Channel Url, Channel Title
    # Some exports use different capitalisation — normalise to lowercase keys.
    for row in reader:
        normalised = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        channel_id = normalised.get("channel id") or normalised.get("channelid") or ""
        title = normalised.get("channel title") or normalised.get("channeltitle") or ""

        if not channel_id.startswith("UC"):
            failed += 1
            errors.append(f"Row skipped — invalid channel ID: {channel_id!r}")
            continue

        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

        # Already subscribed
        if db.query(Feed).filter(Feed.url == feed_url, Feed.user_id == current_user.id).first():
            skipped += 1
            continue

        # Plan feed-count limit
        if plan_limits.max_feeds is not None and current_count + added >= plan_limits.max_feeds:
            errors.append("Plan feed limit reached — remaining channels skipped")
            break

        feed = Feed(url=feed_url, title=title or None, user_id=current_user.id)
        db.add(feed)
        db.flush()
        added += 1

    db.commit()

    # Kick off initial article fetch for newly added feeds (best-effort, non-blocking)
    if added > 0:
        from ..tasks import refresh_feed_by_id
        new_feeds = (
            db.query(Feed)
            .filter(Feed.user_id == current_user.id, Feed.last_fetched_at.is_(None))
            .limit(added)
            .all()
        )
        for feed in new_feeds:
            try:
                refresh_feed_by_id.delay(feed.id)
            except Exception as exc:
                logger.warning("Failed to enqueue refresh for feed %d: %s", feed.id, exc)

    return OPMLImportResult(added=added, skipped=skipped, failed=failed, errors=errors)
