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


def test_health_capabilities_not_rate_limited(client):
    # Hammer free endpoints — none should ever produce a 429.
    for _ in range(5):
        for path in ("/api/health", "/api/capabilities"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_metrics_endpoints_still_answer(client):
    """Metrics are metered now (see test_metrics_endpoints_are_rate_limited),
    but the default bucket is wide enough that ordinary scraping is
    unaffected — `client` uses the default burst."""
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
    exhaust RAM.

    Sized to land BETWEEN the two caps — over MAX_FILE_SIZE (so the image
    field is rejected) but under MAX_REQUEST_BYTES (so the request still
    reaches the route). Above the outer cap it is the body-size middleware
    that answers, with 413; that path is asserted separately below.
    """
    import main as server_main

    cap = server_main.MAX_FILE_SIZE
    oversized = b"\xff\xd8\xff\xe0" + b"\x00" * (cap + 512 * 1024)
    assert len(oversized) < server_main.MAX_REQUEST_BYTES, (
        "test body must stay under the global cap to exercise _read_upload"
    )
    r = client.post(
        "/api/analyze",
        files={"image": ("huge.jpg", oversized, "image/jpeg")},
    )
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_body_over_global_cap_gets_413_on_any_route(client):
    """The global body cap applies to every route and content type, not just
    multipart image uploads.

    JSON endpoints buffer the whole body before Pydantic runs, so before this
    middleware existed a field validator could not refuse an oversized one —
    /api/chat and friends would read an arbitrarily large body into memory.
    """
    import main as server_main

    payload = {"session_id": "s", "message": "x" * (server_main.MAX_REQUEST_BYTES + 1)}
    r = client.post("/api/chat", json=payload)
    assert r.status_code == 413, r.text
    assert "too large" in r.json()["detail"].lower()


def test_metrics_endpoints_are_rate_limited(rate_limited_client):
    """/api/metrics* used to be exempt from the limiter while being the most
    expensive unauthenticated request in the API (full-table SQLite
    aggregates on the event loop). Exempting it made it a free amplifier."""
    first = rate_limited_client.get("/api/metrics")
    assert first.status_code == 200, first.text
    # The `rate_limited_client` fixture runs a 1 req/s bucket.
    statuses = [rate_limited_client.get("/api/metrics").status_code for _ in range(3)]
    assert 429 in statuses, f"metrics never throttled: {statuses}"


def test_client_supplied_trace_id_is_bounded_and_sanitised(client):
    """The trace id is echoed in a response header and stamped on every log
    line for the request, so an unbounded or arbitrary-alphabet value from
    the client would land in both."""
    good = client.get("/api/health", headers={"X-Trace-ID": "abc-123_XYZ.9"})
    assert good.headers["X-Trace-ID"] == "abc-123_XYZ.9"

    # Non-ASCII is omitted deliberately: the HTTP client refuses to encode it,
    # so it never reaches the server. These are the values that do.
    for hostile in ("x" * 500, "trace id with spaces", "a\tb", "id;drop=1"):
        r = client.get("/api/health", headers={"X-Trace-ID": hostile})
        echoed = r.headers["X-Trace-ID"]
        assert echoed != hostile, f"echoed hostile trace id verbatim: {hostile!r}"
        assert len(echoed) == 32 and echoed.isalnum(), echoed


def test_rate_limit_burst_capacity_absorbs_short_bursts(client):
    """``client`` keeps the default capacity (5). Five back-to-back
    POSTs to /api/feedback must all succeed even though the steady-
    state rate is 1 req/s — that's what the burst bucket is for.
    """
    body = {"scan_id": "burst-scan-001", "correct": True}
    for i in range(5):
        r = client.post("/api/feedback", json=body)
        assert r.status_code in (200, 422), f"burst[{i}] got {r.status_code}: {r.text}"
