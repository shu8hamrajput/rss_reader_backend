import pytest

from app.services.url_safety import assert_public_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:6379/",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[fe80::1]/",
])
def test_rejects_private_and_loopback_ip_literals(url):
    with pytest.raises(ValueError):
        assert_public_url(url)


@pytest.mark.parametrize("url", [
    "ftp://example.com",
    "file:///etc/passwd",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(ValueError):
        assert_public_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com/feed.xml",
    "http://93.184.216.34/",  # example.com's public IP
])
def test_accepts_public_targets(url):
    assert_public_url(url)


def test_unresolvable_host_does_not_raise():
    # DNS resolution failure isn't treated as unsafe — let the HTTP call fail naturally.
    assert_public_url("https://this-domain-does-not-exist.invalid/feed.xml")
