"""CLI entry point.

    python -m app.services.parser_gen generate <feed_or_article_url> [--llm] [--samples N]
    python -m app.services.parser_gen improvise <slug> [--llm] [--feedback "TEXT"] [--url URL]...
    python -m app.services.parser_gen approve <slug>
    python -m app.services.parser_gen process-requests [--llm] [--samples N]
"""
import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone

from app.config import settings
from app.services.fetchers._common import strip_and_select

from . import codegen, heuristics, samples
from .proposal import SelectorProposal


def _gather_samples(urls: list[str]) -> list[tuple[str, str]]:
    fetched = []
    for url in urls:
        html = samples.fetch_html(url)
        if html:
            fetched.append((url, html))
        else:
            print(f"  ! failed to fetch {url}", file=sys.stderr)
    return fetched


def _propose(html_samples, use_llm, current=None, feedback=None) -> SelectorProposal:
    hint = heuristics.propose_selectors(html_samples)
    if not use_llm:
        return hint
    from . import llm

    return llm.propose_selectors(html_samples, current=current, feedback=feedback, hint=hint)


def _print_proposal(proposal: SelectorProposal) -> None:
    print(f"Proposed content_selectors: {proposal.content_selectors}")
    print(f"Proposed noise_selectors: {proposal.noise_selectors}")
    print(f"Reasoning: {proposal.reasoning}")


def _print_extraction(label: str, fetched, content_selectors, noise_selectors) -> None:
    print(f"{label}:")
    for url, html in fetched:
        extracted = strip_and_select(html, tuple(content_selectors), tuple(noise_selectors))
        print(f"  {url}: {len(extracted) if extracted else 0} chars extracted")


def cmd_generate(args: argparse.Namespace) -> int:
    domain = samples.domain_from_url(args.url)
    slug = codegen.slug_for_domain(domain)
    pattern = codegen.domain_pattern(domain)

    article_urls, is_feed = samples.sample_article_urls(args.url, args.samples)
    if not article_urls:
        print("error: no article URLs found for the given URL", file=sys.stderr)
        return 1

    fetched = _gather_samples(article_urls)
    if not fetched:
        print("error: could not fetch any sample pages", file=sys.stderr)
        return 1

    if codegen.candidate_path(slug).exists() or codegen.active_path(slug).exists():
        print(f"warning: overwriting existing candidate for '{slug}'", file=sys.stderr)

    html_samples = [html for _, html in fetched]
    proposal = _propose(html_samples, args.llm)
    _print_proposal(proposal)
    _print_extraction("Extraction", fetched, proposal.content_selectors, proposal.noise_selectors)

    meta = {
        "domain": domain,
        "feed_url": args.url if is_feed else None,
        "sample_urls": [url for url, _ in fetched],
        "mode": "llm" if args.llm else "heuristic",
        "model": settings.parser_gen_model if args.llm else None,
        "reasoning": proposal.reasoning,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iteration": 1,
    }
    source = codegen.render_module(proposal, meta, pattern)
    path = codegen.write_candidate(slug, source)
    print(f"Wrote candidate: {path}")
    print(f"Next: review the file, then run `approve {slug}` to activate it.")
    return 0


def cmd_improvise(args: argparse.Namespace) -> int:
    slug = args.slug
    path = codegen.candidate_path(slug)
    if not path.exists():
        path = codegen.active_path(slug)
    if not path.exists():
        print(f"error: no candidate or active module for '{slug}' — run `generate` first", file=sys.stderr)
        return 1

    attrs = codegen.load_module_attrs(path)
    current = SelectorProposal(
        content_selectors=list(attrs["content_selectors"]),
        noise_selectors=list(attrs["noise_selectors"]),
        reasoning=attrs["meta"].get("reasoning", ""),
    )

    sample_urls = list(attrs["meta"].get("sample_urls") or [])
    for url in args.url:
        if url not in sample_urls:
            sample_urls.append(url)

    if not sample_urls:
        print(f"error: no sample URLs stored for '{slug}' — pass --url", file=sys.stderr)
        return 1

    fetched = _gather_samples(sample_urls)
    if not fetched:
        print("error: could not fetch any sample pages", file=sys.stderr)
        return 1

    _print_extraction("Before", fetched, attrs["content_selectors"], attrs["noise_selectors"])

    html_samples = [html for _, html in fetched]
    proposal = _propose(html_samples, args.llm, current=current, feedback=args.feedback)
    _print_proposal(proposal)
    _print_extraction("After", fetched, proposal.content_selectors, proposal.noise_selectors)

    meta = dict(attrs["meta"])
    meta["sample_urls"] = sample_urls
    meta["mode"] = "llm" if args.llm else "heuristic"
    meta["model"] = settings.parser_gen_model if args.llm else None
    meta["reasoning"] = proposal.reasoning
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    meta["iteration"] = int(meta.get("iteration", 1)) + 1

    pattern = attrs["domain_pattern"] or codegen.domain_pattern(meta["domain"])
    source = codegen.render_module(proposal, meta, pattern)
    out_path = codegen.write_candidate(slug, source)
    print(f"Wrote candidate: {out_path}")
    print(f"Next: review the file, then run `approve {slug}` to activate it.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        path = codegen.approve(args.slug)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Approved: {path}")
    print("Restart `make dev` / `make worker` so the new module is imported and self-registers.")
    return 0


def _process_pending_requests(db, use_llm: bool, samples_n: int) -> int:
    """Generate/refine candidate fetchers for each domain with pending parser requests.

    Returns the number of domains processed (written as candidates). Shared by
    `cmd_process_requests` and tests, which pass in their own session.
    """
    from app.models import ParserRequest

    pending = db.query(ParserRequest).filter(ParserRequest.status == "pending").all()
    if not pending:
        print("No pending parser requests.")
        return 0

    by_domain: dict[str, list] = defaultdict(list)
    for req in pending:
        by_domain[req.domain].append(req)

    processed = 0
    for domain, reqs in by_domain.items():
        print(f"\n=== {domain} ({len(reqs)} request(s)) ===")
        slug = codegen.slug_for_domain(domain)
        pattern = codegen.domain_pattern(domain)

        urls = list(dict.fromkeys(req.url for req in reqs))[:samples_n]
        fetched = _gather_samples(urls)
        if not fetched:
            print(f"  ! could not fetch any sample pages for {domain}, marking failed", file=sys.stderr)
            for req in reqs:
                req.status = "failed"
                req.processed_at = datetime.now(timezone.utc)
            db.commit()
            continue

        existing_path = codegen.candidate_path(slug)
        if not existing_path.exists():
            existing_path = codegen.active_path(slug)
        current = None
        iteration = 1
        domain_pattern = pattern
        if existing_path.exists():
            attrs = codegen.load_module_attrs(existing_path)
            current = SelectorProposal(
                content_selectors=list(attrs["content_selectors"]),
                noise_selectors=list(attrs["noise_selectors"]),
                reasoning=attrs["meta"].get("reasoning", ""),
            )
            iteration = int(attrs["meta"].get("iteration", 1)) + 1
            domain_pattern = attrs["domain_pattern"] or pattern

        feedback = "; ".join(req.note for req in reqs if req.note) or None

        html_samples = [html for _, html in fetched]
        proposal = _propose(html_samples, use_llm, current=current, feedback=feedback)
        _print_proposal(proposal)
        _print_extraction("Extraction", fetched, proposal.content_selectors, proposal.noise_selectors)

        meta = {
            "domain": domain,
            "feed_url": None,
            "sample_urls": [url for url, _ in fetched],
            "mode": "llm" if use_llm else "heuristic",
            "model": settings.parser_gen_model if use_llm else None,
            "reasoning": proposal.reasoning,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
        }
        source = codegen.render_module(proposal, meta, domain_pattern)
        path = codegen.write_candidate(slug, source)
        print(f"  Wrote candidate: {path}")

        for req in reqs:
            req.status = "processed"
            req.candidate_slug = slug
            req.processed_at = datetime.now(timezone.utc)
        db.commit()
        processed += 1

    if processed:
        print("\nNext: review the candidate(s), then run `make approve-parser SLUG=<slug>`.")
    return processed


def cmd_process_requests(args: argparse.Namespace) -> int:
    """Generate/refine candidate fetchers from pending user `request-parser` submissions."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _process_pending_requests(db, args.llm, args.samples)
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.services.parser_gen")
    sub = parser.add_subparsers(dest="command", required=True)

    p_generate = sub.add_parser("generate", help="Generate a candidate fetcher from a feed or article URL")
    p_generate.add_argument("url")
    p_generate.add_argument("--llm", action="store_true", help="Use the LLM-assisted proposer (LlamaIndex + Anthropic)")
    p_generate.add_argument("--samples", type=int, default=3)
    p_generate.set_defaults(func=cmd_generate)

    p_improvise = sub.add_parser("improvise", help="Refine an existing candidate or active fetcher")
    p_improvise.add_argument("slug")
    p_improvise.add_argument("--llm", action="store_true", help="Use the LLM-assisted proposer (LlamaIndex + Anthropic)")
    p_improvise.add_argument("--feedback", default=None)
    p_improvise.add_argument("--url", action="append", default=[])
    p_improvise.set_defaults(func=cmd_improvise)

    p_approve = sub.add_parser("approve", help="Promote a candidate to the active fetcher")
    p_approve.add_argument("slug")
    p_approve.set_defaults(func=cmd_approve)

    p_process = sub.add_parser("process-requests", help="Generate/refine candidates from pending user parser requests")
    p_process.add_argument("--llm", action="store_true", help="Use the LLM-assisted proposer (LlamaIndex + Anthropic)")
    p_process.add_argument("--samples", type=int, default=3)
    p_process.set_defaults(func=cmd_process_requests)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
