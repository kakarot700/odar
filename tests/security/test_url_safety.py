"""SECURITY suite: SSRF and fetch-policy defenses (offline, deterministic)."""

import pytest

from odar.url_safety import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    UnsafeURLError,
    validate_redirect_chain,
    validate_url,
)


HOSTILE_URLS = [
    "http://127.0.0.1/x",
    "http://localhost:8888/x",
    "http://0.0.0.0/x",
    "http://10.0.0.5/x",
    "http://172.16.0.9/x",
    "http://192.168.1.1/x",
    "http://169.254.169.254/latest/meta-data",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://[::1]/x",
    "http://[fc00::1]/x",
    "http://foo.internal/x",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://user:pass@example.com/x",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_urls_refused(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url, resolve_dns=False)


def test_public_urls_allowed():
    safe = validate_url("https://en.wikipedia.org/wiki/Science", resolve_dns=False)
    assert safe.host == "en.wikipedia.org"
    assert safe.scheme == "https"


def test_redirect_chain_validates_every_hop():
    # Redirect target is loopback: must be refused even though the origin is public.
    with pytest.raises(UnsafeURLError):
        validate_redirect_chain("https://example.com/start", ["http://127.0.0.1/admin"])


def test_redirect_chain_length_limited():
    hops = [f"https://example.com/hop{i}" for i in range(MAX_REDIRECTS + 2)]
    with pytest.raises(UnsafeURLError):
        validate_redirect_chain("https://example.com/start", hops)


def test_fetch_policy_constants_are_sane():
    assert MAX_RESPONSE_BYTES <= 8 * 1024 * 1024
    assert 0 < MAX_REDIRECTS <= 5
