from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import GeneratedCandidate, User
from ..schemas import CandidateDetail, GenerateFetcherRequest, GeneratedCandidateResponse
from ..services.fetchers import _registry
from ..services.parser_gen import codegen, samples
from ..tasks import generate_fetcher_candidate
from .feeds import _owned_feed

router = APIRouter(prefix="/feeds", tags=["Fetchers"])


def _to_response(candidate: GeneratedCandidate) -> GeneratedCandidateResponse:
    data = GeneratedCandidateResponse.model_validate(candidate)
    if candidate.status == "ready":
        path = codegen.candidate_path(candidate.slug)
        if path.exists():
            attrs = codegen.load_module_attrs(path)
            meta = attrs["meta"]
            data.candidate = CandidateDetail(
                domain=meta.get("domain", candidate.domain),
                slug=candidate.slug,
                mode=meta.get("mode"),
                reasoning=meta.get("reasoning", ""),
                content_selectors=attrs["content_selectors"],
                noise_selectors=attrs["noise_selectors"],
                sample_urls=list(meta.get("sample_urls") or []),
                generated_at=meta.get("generated_at", ""),
                iteration=int(meta.get("iteration", 1)),
                before_chars=dict(meta.get("before_chars") or {}),
                after_chars=dict(meta.get("after_chars") or {}),
            )
    return data


@router.post(
    "/{feed_id}/generate-fetcher",
    response_model=GeneratedCandidateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a custom fetcher candidate for a failing feed (admin only)",
)
def generate_fetcher(
    feed_id: int,
    payload: GenerateFetcherRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    feed = _owned_feed(feed_id, current_user, db)
    article_urls, _ = samples.sample_article_urls(feed.url, 3)
    if not article_urls:
        raise HTTPException(status_code=400, detail="No article URLs found for this feed")

    domain = samples.domain_from_url(article_urls[0])
    slug = codegen.slug_for_domain(domain)

    candidate = GeneratedCandidate(feed_id=feed.id, domain=domain, slug=slug, status="pending")
    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    generate_fetcher_candidate.delay(candidate.id, feed.url, payload.use_llm)
    return _to_response(candidate)


@router.get(
    "/{feed_id}/candidates",
    response_model=list[GeneratedCandidateResponse],
    summary="List generated fetcher candidates for a feed (admin only)",
)
def list_candidates(
    feed_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    feed = _owned_feed(feed_id, current_user, db)
    rows = (
        db.query(GeneratedCandidate)
        .filter(GeneratedCandidate.feed_id == feed.id)
        .order_by(GeneratedCandidate.created_at.desc())
        .all()
    )
    return [_to_response(row) for row in rows]


@router.post(
    "/{feed_id}/candidates/{candidate_id}/approve",
    response_model=GeneratedCandidateResponse,
    summary="Approve a generated fetcher candidate and hot-load it (admin only)",
)
def approve_candidate(
    feed_id: int,
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    feed = _owned_feed(feed_id, current_user, db)
    candidate = (
        db.query(GeneratedCandidate)
        .filter(GeneratedCandidate.id == candidate_id, GeneratedCandidate.feed_id == feed.id)
        .first()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if candidate.status != "ready":
        raise HTTPException(status_code=409, detail=f"Candidate is not ready (status={candidate.status})")

    attrs = codegen.load_module_attrs(codegen.candidate_path(candidate.slug))
    pattern = attrs["domain_pattern"]

    active_path = codegen.approve(candidate.slug)
    if pattern:
        _registry.unregister(pattern)
    _registry.register_from_path(active_path)

    candidate.status = "approved"
    feed.fetch_failure_count = 0
    db.commit()
    db.refresh(candidate)
    return _to_response(candidate)
