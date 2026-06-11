"""Auto-discovers and registers approved generated fetchers.

Each top-level module in this package that defines `_DOMAIN_PATTERN` and
`fetch` is registered with the fetcher registry. Subpackages (notably
`candidates/`) are skipped, so candidates are inert until promoted here via
`python -m app.services.parser_gen approve <slug>` (a file move).
"""
import importlib
import pkgutil

from .._registry import register

for _info in pkgutil.iter_modules(__path__):
    if _info.ispkg:
        continue
    _module = importlib.import_module(f"{__name__}.{_info.name}")
    _pattern = getattr(_module, "_DOMAIN_PATTERN", None)
    _fetch = getattr(_module, "fetch", None)
    if _pattern and _fetch:
        register(_pattern, _fetch)
