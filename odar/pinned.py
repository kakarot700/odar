"""Connection pinning: closes the DNS TOCTOU / rebinding window.

``validate_url(resolve_dns=True)`` checks every address a name resolves to.
Without pinning, the *connection* could then re-resolve the name and hit a
different (attacker-rebound) address.  This module binds the actual socket
connection to the VALIDATED address set:

* a thread-local pin is installed around each request hop;
* while the pin is active, ``socket.getaddrinfo`` for that host returns ONLY
  the validated IPs - a re-resolution returning internal addresses is
  physically ignored;
* TLS verification is unchanged (hostname checks stay intact).

Honest scope: this is application-layer pinning.  Full defense-in-depth
against SSRF additionally requires network-layer egress policy in the
deployment environment (see docs/SECURITY.md and the example
``deploy/network-policy.yaml``); ODAR does not claim complete protection
from application checks alone.
"""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from typing import Iterable, Iterator, List, Optional

_PINNED = threading.local()


def _current_pin() -> Optional[dict]:
    return getattr(_PINNED, "pin", None)


def _pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    pin = _current_pin()
    if pin is not None and isinstance(host, str) and host.lower() == pin["host"].lower():
        infos = []
        for ip in pin["ips"]:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port, 0, 0) if fam == socket.AF_INET6 else (ip, port)
            infos.append((fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        if infos:
            return infos
    return _REAL_GETADDRINFO(host, port, family, type, proto, flags)


_REAL_GETADDRINFO = socket.getaddrinfo


@contextmanager
def pinned_resolution(host: str, ips: Iterable[str]) -> Iterator[None]:
    """Scope in which ``host`` may only connect to ``ips``."""
    previous = getattr(_PINNED, "pin", None)
    _PINNED.pin = {"host": host, "ips": [ip for ip in ips if ip]}
    socket.getaddrinfo = _pinned_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        _PINNED.pin = previous
        if previous is None:
            socket.getaddrinfo = _REAL_GETADDRINFO  # type: ignore[assignment]


def resolve_within_pin(host: str, port: int, allowed_ips: Iterable[str]) -> List[str]:
    """Return the addresses a connection to ``host:port`` would use while the
    given IPs are pinned - useful for adversarial tests of rebinding."""
    with pinned_resolution(host, allowed_ips):
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})
