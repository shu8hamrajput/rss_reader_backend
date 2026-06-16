import asyncio

from app.services.fetchers._default import extract_content, fetch


def test_fetch_rejects_private_address():
    assert asyncio.run(fetch("http://127.0.0.1/admin")) is None


def test_extract_content_from_article_tag():
    html = "<html><body><article>" + ("Lorem ipsum dolor sit amet. " * 20) + "</article></body></html>"
    result = extract_content(html)
    assert result is not None
    assert "<article>" in result


def test_extract_content_falls_back_to_body():
    html = "<html><body><div>" + ("Just some plain text content here. " * 20) + "</div></body></html>"
    result = extract_content(html)
    assert result is not None
    assert "<body>" in result


def test_extract_content_strips_unwanted_tags():
    html = (
        "<html><body><article>"
        + ("Lorem ipsum dolor sit amet. " * 20)
        + "<script>alert('x')</script><nav>Nav</nav>"
        + "</article></body></html>"
    )
    result = extract_content(html)
    assert "<script>" not in result
    assert "<nav>" not in result


def test_extract_content_empty_html_returns_none():
    assert extract_content("") is None
