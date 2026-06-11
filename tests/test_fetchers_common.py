from app.services.fetchers._common import clean_soup, strip_and_select


def test_clean_soup_decomposes_generic_noise_tags():
    html = "<html><body><script>x</script><nav>Nav</nav><article>Hello</article></body></html>"
    soup = clean_soup(html)
    assert soup.find("script") is None
    assert soup.find("nav") is None
    assert soup.find("article") is not None


def test_strip_and_select_matches_first_qualifying_selector():
    html = "<html><body><div class='content'>" + ("Lorem ipsum dolor sit amet. " * 20) + "</div></body></html>"
    result = strip_and_select(html, (".missing", ".content"))
    assert result is not None
    assert "class=\"content\"" in result
    assert "Lorem ipsum" in result


def test_strip_and_select_removes_noise_within_matched_element():
    html = (
        "<html><body><article>"
        + ("Lorem ipsum dolor sit amet. " * 20)
        + "<div class='related-articles'>Related stuff</div>"
        + "</article></body></html>"
    )
    result = strip_and_select(html, ("article",), (".related-articles",))
    assert result is not None
    assert "related-articles" not in result
    assert "Lorem ipsum" in result


def test_strip_and_select_respects_min_chars():
    html = "<html><body><article>too short</article><main>" + ("Lorem ipsum dolor sit amet. " * 20) + "</main></body></html>"
    result = strip_and_select(html, ("article", "main"))
    assert result is not None
    assert "<main>" in result


def test_strip_and_select_returns_none_when_nothing_matches():
    html = "<html><body><div>" + ("Lorem ipsum dolor sit amet. " * 20) + "</div></body></html>"
    assert strip_and_select(html, (".missing", "article")) is None
