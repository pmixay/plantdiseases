"""Fail-soft behaviour of the MaxMind geo resolver (no .mmdb in CI)."""
from __future__ import annotations

import geoip


class _FakeRequest:
    def __init__(self, headers=None, host=None):
        self.headers = headers or {}

        class _Client:
            pass
        self.client = _Client()
        self.client.host = host


def test_valid_code():
    assert geoip._valid_code("us") == "US"
    assert geoip._valid_code("USA") is None  # not 2 letters
    assert geoip._valid_code("1A") is None   # not alpha
    assert geoip._valid_code(None) is None


def test_private_ip_not_public():
    assert geoip._is_public_ip("8.8.8.8") is True
    assert geoip._is_public_ip("10.0.0.1") is False
    assert geoip._is_public_ip("127.0.0.1") is False
    assert geoip._is_public_ip("not-an-ip") is False


def test_country_header_fallback_without_mmdb():
    # No .mmdb in CI + a private IP → the CDN country header is the only signal.
    req = _FakeRequest(headers={"cf-ipcountry": "PL"}, host="10.0.0.5")
    assert geoip.detect_country(req, "10.0.0.5") == "PL"
    loc = geoip.detect_location(req, "10.0.0.5")
    assert loc is not None and loc.country == "PL"
    # Header path can't supply a subdivision.
    assert loc.subdivision is None


def test_no_signal_returns_none():
    req = _FakeRequest(headers={}, host="10.0.0.5")
    assert geoip.detect_country(req, "10.0.0.5") is None
