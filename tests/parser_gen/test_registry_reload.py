import asyncio

from app.services.fetchers import _registry

_MODULE_SRC = '''
_DOMAIN_PATTERN = r"hotreload_test\\.com/"


async def fetch(url):
    return "hot-reloaded-content"
'''


def test_register_from_path_and_unregister(tmp_path):
    path = tmp_path / "hotreload_test.py"
    path.write_text(_MODULE_SRC, encoding="utf-8")

    before = len(_registry._registry)
    try:
        assert _registry.register_from_path(path) is True
        assert len(_registry._registry) == before + 1

        fetcher = _registry._resolve("https://hotreload_test.com/article")
        assert asyncio.run(fetcher("https://hotreload_test.com/article")) == "hot-reloaded-content"
    finally:
        _registry.unregister(r"hotreload_test\.com/")

    assert len(_registry._registry) == before


def test_register_from_path_missing_attrs(tmp_path):
    path = tmp_path / "incomplete.py"
    path.write_text("X = 1\n", encoding="utf-8")

    before = len(_registry._registry)
    assert _registry.register_from_path(path) is False
    assert len(_registry._registry) == before
