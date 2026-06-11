"""Candidate fetchers awaiting approval.

This subpackage is intentionally NOT auto-discovered by `generated/__init__.py`
(`pkgutil.iter_modules` reports it with `ispkg=True`, which the discovery loop
skips). Promote a candidate with `python -m app.services.parser_gen approve
<slug>`, which moves `candidates/<slug>.py` to `generated/<slug>.py`.
"""
