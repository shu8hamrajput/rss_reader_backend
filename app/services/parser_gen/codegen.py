"""Render, write, and approve generated fetcher modules."""
import ast
import importlib.util
import re
from pathlib import Path

from .proposal import SelectorProposal

_FETCHERS_DIR = Path(__file__).resolve().parent.parent / "fetchers"

_MODULE_TEMPLATE = '''"""{domain} fetcher — {mode_label} mode, generated {generated_at}.
{reasoning_summary}
"""
import httpx

from app.services.fetchers._common import strip_and_select
from app.services.fetchers._default import fetch as default_fetch

_HEADERS = {{"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}}

_DOMAIN_PATTERN = {domain_pattern!r}  # _registry uses pattern.search(url)

_CONTENT_SELECTORS = {content_selectors!r}
_NOISE_SELECTORS = {noise_selectors!r}

_META = {meta!r}


def _extract(html: str) -> str | None:
    if not _CONTENT_SELECTORS:
        return None
    return strip_and_select(html, _CONTENT_SELECTORS, _NOISE_SELECTORS)


async def fetch(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        content = _extract(resp.text)
        if content:
            return content
        return await default_fetch(url)
    except Exception:
        return None
'''


def slug_for_domain(domain: str) -> str:
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return re.sub(r"[^a-z0-9]+", "_", domain).strip("_")


def domain_pattern(domain: str) -> str:
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return re.escape(domain) + "/"


def render_module(proposal: SelectorProposal, meta: dict, pattern: str) -> str:
    mode_label = "heuristic" if meta["mode"] == "heuristic" else f"LLM-assisted ({meta['model']})"
    reasoning_summary = proposal.reasoning.splitlines()[0] if proposal.reasoning else ""
    reasoning_summary = reasoning_summary.replace('"""', "'''")

    source = _MODULE_TEMPLATE.format(
        domain=meta["domain"],
        mode_label=mode_label,
        generated_at=meta["generated_at"],
        reasoning_summary=reasoning_summary,
        domain_pattern=pattern,
        content_selectors=tuple(proposal.content_selectors),
        noise_selectors=tuple(proposal.noise_selectors),
        meta=meta,
    )
    ast.parse(source)  # sanity check — raise before writing an unimportable module
    return source


def generated_dir() -> Path:
    return _FETCHERS_DIR / "generated"


def candidates_dir() -> Path:
    return generated_dir() / "candidates"


def candidate_path(slug: str) -> Path:
    return candidates_dir() / f"{slug}.py"


def active_path(slug: str) -> Path:
    return generated_dir() / f"{slug}.py"


def write_candidate(slug: str, source: str) -> Path:
    path = candidate_path(slug)
    path.write_text(source, encoding="utf-8")
    return path


def approve(slug: str) -> Path:
    src = candidate_path(slug)
    if not src.exists():
        raise FileNotFoundError(f"no candidate for '{slug}' at {src}")
    dst = active_path(slug)
    src.replace(dst)
    return dst


def load_module_attrs(path: Path) -> dict:
    """Load `_CONTENT_SELECTORS`/`_NOISE_SELECTORS`/`_DOMAIN_PATTERN`/`_META` from a
    candidate or active module without importing the whole `app.services.fetchers` package."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        "content_selectors": tuple(getattr(module, "_CONTENT_SELECTORS", ())),
        "noise_selectors": tuple(getattr(module, "_NOISE_SELECTORS", ())),
        "domain_pattern": getattr(module, "_DOMAIN_PATTERN", None),
        "meta": dict(getattr(module, "_META", {})),
    }
