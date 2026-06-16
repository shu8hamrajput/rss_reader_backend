"""SSRF guard for outbound fetches of user/feed-supplied URLs.

Feed URLs, article URLs, and discovery targets all come from untrusted
sources (the subscribing user, or third-party feed content) and are fetched
server-side. Without validation, a URL like `http://169.254.169.254/` or
`http://127.0.0.1:6379/` would be fetched in-process.
"""
import ipaddress
import socket
from urllib.parse import urlparse


def _is_unsafe_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_public_url(url: str) -> None:
    """Raise ValueError if *url* targets a non-public address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if _is_unsafe_ip(host):
            raise ValueError(f"URL targets a non-public address: {host}")
        return

    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return

    if any(_is_unsafe_ip(addr) for addr in resolved):
        raise ValueError(f"URL resolves to a non-public address: {host}")
