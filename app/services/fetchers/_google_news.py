"""
Google News fetcher.

Google News RSS article URLs (news.google.com/rss/articles/...) are opaque
tokens — no HTTP redirect leads to the real article. This fetcher uses the
`googlenewsdecoder` package to resolve the token to the canonical article URL,
then delegates to the default scraper to extract content from that URL.
"""
import asyncio

from ._default import fetch as default_fetch


async def fetch(url: str) -> str | None:
    try:
        from googlenewsdecoder import new_decoderv1

        # new_decoderv1 is synchronous and makes HTTP calls; run off the event loop
        result = await asyncio.to_thread(new_decoderv1, url)

        if not result.get("status"):
            return None

        real_url = result["decoded_url"]
        return await default_fetch(real_url)
    except Exception:
        return None
