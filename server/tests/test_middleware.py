"""Middleware behaviour: rate limit, security headers, free endpoints."""

from __future__ import annotations


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cache-Control",
    ):
        assert header in r.headers, f"missing header {header}"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_health_version_classes_not_rate_limited(client):
    # Hammer free endpoints — none should ever produce a 429.
    for _ in range(5):
        for path in ("/api/health", "/api/version", "/api/classes"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_metrics_endpoints_not_rate_limited(client):
    for _ in range(5):
        assert client.get("/api/metrics").status_code == 200
        assert client.get("/api/metrics/prometheus").status_code == 200


def test_analyze_rate_limit_returns_429_with_retry_after(rate_limited_client, png_bytes):
    # With a 1 req/s limit, two back-to-back uploads from the same client
    # trip the limiter on the second request.
    files = {"image": ("a.png", png_bytes, "image/png")}
    r1 = rate_limited_client.post("/api/analyze", files=files)
    assert r1.status_code == 200, r1.text

    files2 = {"image": ("b.png", png_bytes, "image/png")}
    r2 = rate_limited_client.post("/api/analyze", files=files2)
    assert r2.status_code == 429, r2.text
    assert "Retry-After" in r2.headers
    assert int(r2.headers["Retry-After"]) >= 1
