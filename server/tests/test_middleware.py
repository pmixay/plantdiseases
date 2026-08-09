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


def test_request_log_uses_route_template_and_server_correlation_id(client, caplog):
    import logging

    client_trace = "CLIENT_TRACE_SENTINEL_A91F"
    query_secret = "QUERY_TOKEN_SENTINEL_14B7"
    auth_secret = "AUTHORIZATION_SENTINEL_70CC"
    forwarded_address = "198.51.100.187"
    caplog.set_level(logging.INFO, logger="plantdiseases")

    response = client.get(
        f"/api/health?token={query_secret}",
        headers={
            "Authorization": f"Bearer {auth_secret}",
            "X-Trace-ID": client_trace,
            "X-Request-ID": client_trace,
            "X-Forwarded-For": forwarded_address,
        },
    )
    assert response.status_code == 200
    server_trace = response.headers["X-Trace-ID"]
    assert server_trace != client_trace
    assert len(server_trace) == 32
    assert all(character in "0123456789abcdef" for character in server_trace)

    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "plantdiseases"
    )
    assert "REQUEST GET /api/health -> 200" in application_logs
    for forbidden in (
        client_trace,
        query_secret,
        auth_secret,
        forwarded_address,
    ):
        assert forbidden not in application_logs


def test_health_capabilities_not_rate_limited(client):
    # Hammer free endpoints — none should ever produce a 429.
    for _ in range(5):
        for path in ("/api/health", "/api/capabilities"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_metrics_endpoints_not_rate_limited(client):
    for _ in range(5):
        assert client.get("/api/metrics").status_code == 200
        assert client.get("/api/metrics/prometheus").status_code == 200


def test_public_community_reads_ignore_credentials_but_writes_validate_jwt(
    client, monkeypatch,
):
    import main as server_main

    monkeypatch.setattr(server_main, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(server_main, "API_KEYS", {})
    monkeypatch.setattr(
        server_main, "community_density_grid", lambda *_args, **_kwargs: {"cells": []}
    )
    assert server_main._is_public_community_read(
        "GET", "/api/community/thumb/example.webp"
    )
    assert not server_main._is_public_community_read(
        "POST", "/api/community/thumb/example.webp"
    )

    public = client.get(
        "/api/community/heatmap",
        headers={
            "Authorization": "Bearer a.b.c",
            "X-API-Key": "deliberately-invalid",
        },
        params={"min_lat": 1, "min_lon": 1, "max_lat": 2, "max_lon": 2},
    )
    protected = client.post(
        "/api/community/opinion",
        headers={
            "Authorization": "Bearer a.b.c",
            "X-API-Key": "deliberately-invalid",
        },
        json={"scan_id": "unknownscan01", "suggested_disease": "rust"},
    )
    legacy = client.get(
        "/api/version", headers={"Authorization": "Bearer a.b.c"}
    )

    assert public.status_code == 200
    assert protected.status_code == 401
    assert protected.json()["detail"] == "auth_required"
    assert legacy.status_code == 401


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


def test_gzip_middleware_compresses_large_responses(client):
    """`/openapi.json` is tens of KB of JSON. GZip should kick in for
    clients that advertise Accept-Encoding: gzip."""
    r = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # TestClient transparently decompresses, but the response headers
    # still carry Content-Encoding: gzip when the middleware was active.
    assert r.headers.get("content-encoding") == "gzip", (
        "GZip middleware did not compress a >1 KB response — regression "
        "of the bandwidth optimisation"
    )


def test_cors_allow_headers_is_not_wildcard(client):
    """CORS preflight must NOT allow arbitrary custom headers — wildcard
    `*` would let a malicious origin probe headers we don't accept."""
    r = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    allowed = r.headers.get("access-control-allow-headers", "")
    assert allowed and "*" not in allowed, (
        f"CORS allow_headers is wildcard: {allowed!r}"
    )


def test_oversized_body_rejected_without_full_read(client):
    """`_read_upload` should fail at MAX_FILE_SIZE+1 bytes regardless
    of what the client claims via Content-Length. The total bytes
    actually consumed is bounded so a slowloris-style upload can't
    exhaust RAM."""
    import main as server_main

    cap = server_main.MAX_FILE_SIZE
    # 2 MB over the cap (oversized but not absurd) — the helper should
    # reject without parsing image bytes.
    oversized = b"\xff\xd8\xff\xe0" + b"\x00" * (cap + 2 * 1024 * 1024)
    r = client.post(
        "/api/analyze",
        files={"image": ("huge.jpg", oversized, "image/jpeg")},
    )
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_rate_limit_burst_capacity_absorbs_short_bursts(client):
    """``client`` keeps the default capacity (5). Five back-to-back
    POSTs to /api/feedback must all succeed even though the steady-
    state rate is 1 req/s — that's what the burst bucket is for.
    """
    body = {"feedback_receipt": "x" * 43, "correct": True}
    for i in range(5):
        r = client.post("/api/feedback", json=body)
        assert r.status_code in (200, 422), f"burst[{i}] got {r.status_code}: {r.text}"
