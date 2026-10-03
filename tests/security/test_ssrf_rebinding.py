"""REGRESSION (P1 FIX 10): DNS TOCTOU / rebinding-sensitive behavior.

The actual connection is pinned to the address set that was validated.  A
re-resolution that returns hostile addresses must be ignored.
"""

import socket

from odar.pinned import pinned_resolution, resolve_within_pin
from odar.url_safety import UnsafeURLError, validate_url


class TestConnectionPinning:
    def test_pin_overrides_rebinding_resolution(self):
        # Attacker rebinds example.test to loopback AFTER validation; the
        # pinned connection still resolves only to the validated public IP.
        def evil_getaddrinfo(host, port, *args, **kwargs):
            if host == "example.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
            raise socket.gaierror("no such host")

        real = socket.getaddrinfo
        socket.getaddrinfo = evil_getaddrinfo
        try:
            resolved = resolve_within_pin("example.test", 443, ["93.184.216.34"])
        finally:
            socket.getaddrinfo = real
        assert resolved == ["93.184.216.34"], "pin must ignore the rebound hostile address"

    def test_pin_scoped_to_host(self):
        with pinned_resolution("other.test", ["93.184.216.34"]):
            # Resolution of a DIFFERENT host is untouched by the pin.
            infos = socket.getaddrinfo("localhost", 80, proto=socket.IPPROTO_TCP)
            assert infos, "unrelated hosts resolve normally"

    def test_pin_restores_after_scope(self):
        before = socket.getaddrinfo
        with pinned_resolution("example.test", ["93.184.216.34"]):
            pass
        assert socket.getaddrinfo is before

    def test_nested_pins_restore_outer(self):
        with pinned_resolution("a.test", ["1.2.3.4"]):
            with pinned_resolution("b.test", ["5.6.7.8"]):
                resolved = resolve_within_pin("b.test", 80, ["5.6.7.8"])
                assert resolved == ["5.6.7.8"]
            resolved = resolve_within_pin("a.test", 80, ["1.2.3.4"])
            assert resolved == ["1.2.3.4"]


class TestValidationStillCatchesHostileDns:
    def test_dns_resolving_to_private_blocked(self, monkeypatch):
        def evil(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", port))]

        monkeypatch.setattr(socket, "getaddrinfo", evil)
        try:
            validate_url("https://attacker.example/page", resolve_dns=True)
            blocked = False
        except UnsafeURLError:
            blocked = True
        assert blocked, "a name resolving to private space must be refused at validation"

    def test_credentials_in_url_still_refused(self):
        try:
            validate_url("https://user:pass@example.com/", resolve_dns=False)
            blocked = False
        except UnsafeURLError:
            blocked = True
        assert blocked
