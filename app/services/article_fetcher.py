from .fetchers import fetch_content


async def fetch_full_content(url: str) -> str | None:
    return await fetch_content(url)
