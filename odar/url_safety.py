"""SSRF-safe URL validation and fetch policy.

The page-fetching subsystem must be safe for hostile URLs.  Every URL -
including every redirect hop - is validated *after* DNS resolution against a
deny policy covering:

* non-HTTP(S) schemes,
* loopback, private IPv4/IPv6 ranges, link-local ranges,
* cloud metadata endpoints (169.254.169.254 and alts),
* reserved/broadcast/multicast/unspecified addresses,
* internal-style hostnames (``localhost``, ``*.internal``, ``*.local``,
  kubernetes service names),
* literal IPs smuggled in userinfo/port positions.

Redirects are followed manually hop-by-hop with per-hop re-validation
(redirect-based SSRF defense); the original hostname is never trusted after
the first hop.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import List
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2_000_000
ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "text/plain", "application/xhtml+xml", "application/xml", "text/xml"}
)

_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "fd00:ec2::254",
    }
)
_INTERNAL_HOST_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".corp")
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "ip6-localhost", "ip6-loopback", "kubernetes", "kubernetes.default"}
)


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF deny policy."""


@dataclass
class SafeURL:
    """A validated URL plus the resolved addresses it was checked against."""

    url: str
    host: str
    port: int
    scheme: str
    resolved_ips: List[str]


def _host_is_blocked_name(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in _BLOCKED_HOSTNAMES or lowered in _METADATA_HOSTS:
        return True
    if any(lowered.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES):
        return True
    return False


def _ip_is_blocked(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, resolve_dns: bool = True) -> SafeURL:
    """Validate one URL against the deny policy.

    ``resolve_dns=True`` resolves the hostname and validates *every* returned
    address (defense against multi-A-record rebinding where one record is
    public and another is internal).
    """
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURLError("missing or oversized URL")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError(f"unparseable URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme '{scheme}' not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURLError("missing hostname")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in URL are not allowed")

    # Literal-IP hostnames are checked directly.  NOTE: the ipaddress parse
    # and the policy raise must be kept in separate blocks - UnsafeURLError is
    # a ValueError subclass and a combined try/except would swallow the raise.
    literal_ip = None
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _ip_is_blocked(literal_ip):
            raise UnsafeURLError(f"blocked address {host}")
        port = parts.port or (443 if scheme == "https" else 80)
        return SafeURL(url=url, host=host, port=port, scheme=scheme, resolved_ips=[str(literal_ip)])

    if _host_is_blocked_name(host):
        raise UnsafeURLError(f"blocked hostname '{host}'")

    resolved: List[str] = []
    if resolve_dns:
        try:
            infos = socket.getaddrinfo(
                host, parts.port or (443 if scheme == "https" else 80), proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as exc:
            raise UnsafeURLError(f"DNS resolution failed for '{host}': {exc}") from exc
        for info in infos:
            address = str(info[4][0])
            try:
                ip_obj = ipaddress.ip_address(address.split("%")[0])
            except ValueError:
                continue
            if _ip_is_blocked(ip_obj):
                raise UnsafeURLError(f"'{host}' resolves to blocked address {address} (possible rebinding)")
            resolved.append(str(ip_obj))
        if not resolved:
            raise UnsafeURLError(f"'{host}' resolved to no usable addresses")

    port = parts.port or (443 if scheme == "https" else 80)
    return SafeURL(url=url, host=host, port=port, scheme=scheme, resolved_ips=resolved)


def validate_redirect_chain(origin: str, hops: List[str]) -> List[SafeURL]:
    """Validate an origin URL and every redirect hop."""
    if len(hops) > MAX_REDIRECTS:
        raise UnsafeURLError(f"too many redirects ({len(hops)} > {MAX_REDIRECTS})")
    validated = [validate_url(origin)]
    for hop in hops:
        validated.append(validate_url(hop))
    return validated


def is_safe_url(url: str) -> bool:
    """Non-raising convenience predicate (used by gates and tests)."""
    try:
        validate_url(url, resolve_dns=False)
        return True
    except UnsafeURLError:
        return False
