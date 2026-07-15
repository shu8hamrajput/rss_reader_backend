"""
OPML import/export router — pure dispatcher. See ADR-004.

All format-specific logic lives in app/formats/:
  OPMLImporter      → parses .opml / .xml files
  YouTubeCSVImporter→ parses Google Takeout subscriptions.csv
  OPMLExporter      → serialises feeds as OPML XML
  MarkdownExporter  → serialises feeds as Markdown

To add a new import format (Pocket, Instapaper, etc.):
  1. Create app/formats/pocket.py implementing FeedImporter
  2. format_registry.register_importer(PocketImporter())
  3. Done — this router auto-detects it by file extension / MIME type.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..auth import get_current_user
from ..database import get_db
from ..formats import format_registry
from ..models import Category, Feed, User
from ..schemas import OPMLImportResult
from ..services.feed_parser import refresh_feed
from ..services.plans import effective_plan, limits_for

router = APIRouter(prefix="/opml", tags=["OPML"])
logger = logging.getLogger(__name__)

_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


@router.get("/formats", summary="List available import/export formats")
def list_formats():
    return {
        "importers": format_registry.list_importers(),
        "exporters": format_registry.list_exporters(),
    }


@router.post("/import", response_model=OPMLImportResult, summary="Import feeds from a file")
async def import_feeds(
    file: Annotated[UploadFile, File(description="OPML (.opml/.xml) or YouTube CSV (.csv)")],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw = await file.read(_MAX_SIZE + 1)
    if len(raw) > _MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    filename     = file.filename or ""
    content_type = file.content_type or ""
    importer     = format_registry.get_importer(filename, content_type, raw)
    if not importer:
        supported = [ext for i in format_registry.list_importers() for ext in i["extensions"]]
        raise HTTPException(status_code=415, detail=f"Unsupported file type. Supported: {supported}")

    try:
        imported_feeds = await importer.parse(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    plan_limits   = limits_for(effective_plan(current_user))
    current_count = db.query(func.count(Feed.id)).filter(Feed.user_id == current_user.id).scalar() or 0
    category_cache: dict[str, Category] = {}
    added = skipped = failed = 0
    errors: list[str] = []

    for imp_feed in imported_feeds:
        if plan_limits.max_feeds is not None and current_count + added >= plan_limits.max_feeds:
            errors.append("Plan feed limit reached — remaining feeds skipped")
            break

        if db.query(Feed).filter(Feed.url == imp_feed.url, Feed.user_id == current_user.id).first():
            skipped += 1
            continue

        cat: Category | None = None
        if imp_feed.category:
            if imp_feed.category not in category_cache:
                cat = db.query(Category).filter(
                    Category.user_id == current_user.id, Category.name == imp_feed.category
                ).first()
                if not cat:
                    cat = Category(user_id=current_user.id, name=imp_feed.category)
                    db.add(cat)
                    db.flush()
                category_cache[imp_feed.category] = cat
            cat = category_cache[imp_feed.category]

        feed = Feed(url=imp_feed.url, title=imp_feed.title, user_id=current_user.id, discovered_via="opml_import")
        if cat:
            feed.categories.append(cat)
        db.add(feed)
        db.flush()

        try:
            await refresh_feed(feed, db)
            added += 1
        except Exception as exc:
            errors.append(f"{imp_feed.url}: {exc}")
            failed += 1

    db.commit()
    return OPMLImportResult(added=added, skipped=skipped, failed=failed, errors=errors)


@router.get("/export", summary="Export your feeds", response_class=Response)
async def export_feeds(
    format: str = Query("opml", description="Export format: opml, markdown"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exporter = format_registry.get_exporter(format)
    if not exporter:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format {format!r}. Available: {[e['name'] for e in format_registry.list_exporters()]}",
        )

    feeds = (
        db.query(Feed)
        .filter(Feed.user_id == current_user.id)
        .options(selectinload(Feed.categories))
        .limit(10_000)
        .all()
    )

    content  = await exporter.export(feeds, current_user)
    filename = f"feeds{exporter.extension}"
    return Response(
        content=content,
        media_type=exporter.content_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Backward-compat alias — YouTube CSV was previously a separate endpoint
@router.post("/import-youtube", response_model=OPMLImportResult,
             summary="Import YouTube subscriptions CSV (alias for /import)")
async def import_youtube_csv(
    file: Annotated[UploadFile, File()],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await import_feeds(file, db, current_user)
