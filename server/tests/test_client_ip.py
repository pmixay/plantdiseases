"""Regression tests for ``main._client_ip`` — the spoofable-identity fix.

``_client_ip`` is the transient identity used by in-process abuse controls such
as ``RateLimitMiddleware`` and daily write caps. Neither the address nor its
hash is persisted by feedback, account-era community writes, or telemetry.

The bug this file pins down: the reference nginx config in DEPLOYMENT.md sets

    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

and ``$proxy_add_x_forwarded_for`` *appends* the real peer to whatever the
client already sent. So on a request from 198.51.100.9 carrying a forged
``X-Forwarded-For: 203.0.113.7`` header, nginx hands the app

    X-Forwarded-For: 203.0.113.7, 198.51.100.9

Reading the list left-to-right therefore returns the attacker's chosen value.
Behind an appending proxy the only entry the client cannot control is the
*rightmost* one, after discarding the trusted-proxy hops appended by our own
infrastructure.
"""
from __future__ import annotations

import pytest

import main


class _Headers:
    """Minimal case-insensitive stand-in for Starlette's Headers."""

    def __init__(self, **kwargs: str) -> None:
        self._data = {k.lower().replace("_", "-"): v for k, v in kwargs.items()}

    def get(self, key: str, default=None):
        return self._data.get(key.lower(), default)


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Request:
    """``_client_ip`` only touches ``.client.host`` and ``.headers.get``."""

    def __init__(self, peer: str | None, **headers: str) -> None:
        self.client = _Client(peer) if peer is not None else None
        self.headers = _Headers(**headers)


@pytest.fixture
def trusted(monkeypatch):
    """Configure the documented single-nginx-hop topology."""
    monkeypatch.setattr(main, "TRUSTED_PROXIES", {"127.0.0.1"})


def test_forged_leftmost_entry_is_not_trusted(trusted):
    """The core fix: a client-supplied XFF entry must never win.

    nginx appended the true peer (127.0.0.1 here, since it is co-located), so
    the forged 203.0.113.7 sits to its left and must be ignored.
    """
    req = _Request("127.0.0.1", x_forwarded_for="203.0.113.7, 198.51.100.9")
    assert main._client_ip(req) == "198.51.100.9"


def test_multi_hop_discards_all_trusted_hops(monkeypatch):
    """With several trusted hops, the first untrusted address from the right wins."""
    monkeypatch.setattr(main, "TRUSTED_PROXIES", {"127.0.0.1", "10.0.0.0/8"})
    req = _Request(
        "127.0.0.1",
        x_forwarded_for="203.0.113.7, 198.51.100.9, 10.1.2.3, 10.4.5.6",
    )
    assert main._client_ip(req) == "198.51.100.9"


def test_single_entry_from_trusted_proxy_is_honoured(trusted):
    """A non-appending proxy sends exactly one entry — the real client."""
    req = _Request("127.0.0.1", x_forwarded_for="198.51.100.9")
    assert main._client_ip(req) == "198.51.100.9"


def test_untrusted_peer_header_ignored(trusted):
    """Direct connection from a non-proxy: the header is attacker-supplied noise."""
    req = _Request("198.51.100.9", x_forwarded_for="203.0.113.7")
    assert main._client_ip(req) == "198.51.100.9"


def test_no_trusted_proxies_ignores_header(monkeypatch):
    """Default deployment (no proxy configured) must never read XFF."""
    monkeypatch.setattr(main, "TRUSTED_PROXIES", set())
    req = _Request("198.51.100.9", x_forwarded_for="203.0.113.7")
    assert main._client_ip(req) == "198.51.100.9"


def test_no_header_falls_back_to_peer(trusted):
    req = _Request("127.0.0.1")
    assert main._client_ip(req) == "127.0.0.1"


def test_all_entries_trusted_falls_back_to_peer(trusted):
    """Nothing untrusted in the chain — do not invent an identity."""
    req = _Request("127.0.0.1", x_forwarded_for="127.0.0.1")
    assert main._client_ip(req) == "127.0.0.1"


def test_garbage_entries_are_skipped(trusted):
    """Unparseable entries must not become the identity, forged or not."""
    req = _Request("127.0.0.1", x_forwarded_for="not-an-ip, 198.51.100.9, junk")
    assert main._client_ip(req) == "198.51.100.9"


def test_empty_header_falls_back_to_peer(trusted):
    req = _Request("127.0.0.1", x_forwarded_for="   ")
    assert main._client_ip(req) == "127.0.0.1"


def test_missing_client_is_handled(trusted):
    """ASGI scopes can omit ``client`` entirely (e.g. some test transports)."""
    req = _Request(None, x_forwarded_for="203.0.113.7")
    assert main._client_ip(req) == "unknown"


def test_spoofing_cannot_partition_the_rate_limit_bucket(trusted):
    """The property that matters: N forged headers must not yield N identities."""
    seen = {
        main._client_ip(
            _Request("127.0.0.1", x_forwarded_for=f"203.0.113.{i}, 198.51.100.9"),
        )
        for i in range(1, 30)
    }
    assert seen == {"198.51.100.9"}
