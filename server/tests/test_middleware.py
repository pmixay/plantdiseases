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


def test_health_capabilities_not_rate_limited(rate_limited_client):
    # Hammer the free endpoints under the strict 1 req/s, no-burst bucket: a
    # metered route 429s on its second call there, so ten clean answers prove
    # the exemption itself rather than a wide default bucket.
    for _ in range(5):
        for path in ("/api/health", "/api/capabilities"):
            r = rate_limited_client.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"


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


def test_client_supplied_trace_id_is_ignored(client):
    """A caller-selected ID must not become an undeclared stable correlate."""
    # Non-ASCII is omitted deliberately: the HTTP client refuses to encode it,
    # so it never reaches the server. Valid-looking and hostile values are both
    # replaced with fresh server correlation IDs.
    for supplied in (
        "abc-123_XYZ.9",
        "x" * 500,
        "trace id with spaces",
        "a\tb",
        "id;drop=1",
    ):
        response = client.get("/api/health", headers={"X-Trace-ID": supplied})
        generated = response.headers["X-Trace-ID"]
        assert generated != supplied
        assert len(generated) == 32
        assert all(character in "0123456789abcdef" for character in generated)


# ── Unhandled exceptions ──────────────────────────────────────────────
# Before `_unhandled_exception_handler` existed, an exception escaping a
# handler was answered by Starlette's ServerErrorMiddleware: plain-text
# "Internal Server Error", no X-Trace-ID, no access-log line and no error
# metric — the one request an operator most needs to find had nothing to
# grep for. These pin the replacement.

def _client_swallowing_server_errors():
    """A TestClient that returns the 500 instead of re-raising it.

    Starlette re-raises after the handler produces its response, which is
    what makes the traceback reach uvicorn in production; the default
    TestClient turns that into a test error, so we opt out here.
    """
    from fastapi.testclient import TestClient

    import main as server_main

    return TestClient(server_main.app, raise_server_exceptions=False)


def test_unhandled_exception_is_a_logged_counted_json_500_with_a_server_trace_id(
    monkeypatch, caplog,
):
    """One failing handler, every promise the 500 path makes: the shared JSON
    error shape, a server-minted trace id (with or without a client one), no
    leaked exception text, an error-counter increment and an access-log line."""
    import logging

    import main as server_main

    def _boom():
        raise RuntimeError("store exploded")

    before = server_main._snapshot_metrics()["total_errors"]
    monkeypatch.setattr(server_main, "community_stats_snapshot", _boom)

    with caplog.at_level(logging.INFO, logger="plantdiseases"):
        with _client_swallowing_server_errors() as c:
            r = c.get("/api/metrics", headers={"X-Trace-ID": "trace-abc_123"})
            bare = c.get("/api/metrics")

    assert r.status_code == 500
    # The same JSON {"detail": ...} shape every other error on this API uses.
    assert r.json() == {"detail": "Internal server error"}
    # A client-supplied trace id is never honoured (it would be a stable
    # cross-request correlate); the 500 carries the server-minted id instead.
    server_trace = r.headers["X-Trace-ID"]
    assert server_trace != "trace-abc_123"
    assert len(server_trace) == 32
    assert all(character in "0123456789abcdef" for character in server_trace)
    # The exception message must not leak to the client.
    assert "store exploded" not in r.text

    # A request without a client trace id still gets one minted.
    assert bare.status_code == 500
    assert bare.headers.get("X-Trace-ID")

    assert server_main._snapshot_metrics()["total_errors"] == before + 2
    assert any(
        "REQUEST GET /api/metrics -> 500" in rec.getMessage()
        for rec in caplog.records
    ), "the failing request produced no access-log line"
