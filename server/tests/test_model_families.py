"""Model-family routing + capabilities + frontier gating.

Exercises the ``family`` query parameter on /api/analyze, the
/api/capabilities advertisement the app's model picker reads, and the
graceful-degradation contract for a disabled/unprovisioned frontier tier.
The session-wide ``FakePipeline`` (see conftest) stands in for the real
weights; frontier-enabled tests swap a second fake in directly.
"""

from __future__ import annotations

import pytest

import main as server_main


def _analyze(client, png_bytes, family=None):
    params = {} if family is None else {"family": family}
    return client.post(
        "/api/analyze",
        params=params,
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )


# ── /api/capabilities ───────────────────────────────────────────────


def test_capabilities_lists_three_families_and_their_availability(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default"] == "gamma"
    by_id = {f["id"]: f for f in body["families"]}
    assert set(by_id) == {"aqua", "gamma", "frontier"}
    assert by_id["aqua"]["available"] is True
    assert by_id["aqua"]["location"] == "on_device"
    assert by_id["gamma"]["available"] is True
    assert by_id["gamma"]["location"] == "server"
    # Frontier is gated off by default (FRONTIER_ENABLED unset in tests).
    assert by_id["frontier"]["available"] is False
    assert by_id["frontier"]["location"] == "server"


# ── family routing on /api/analyze ──────────────────────────────────


@pytest.mark.parametrize(
    "family",
    [
        pytest.param(None, id="default"),
        pytest.param("gamma", id="explicit"),
        # Forward-compatible: an unknown tier degrades to the default
        # instead of a 400.
        pytest.param("totally-made-up", id="unknown-falls-back"),
    ],
)
def test_analyze_is_served_by_gamma(client, png_bytes, family):
    r = _analyze(client, png_bytes, family=family)
    assert r.status_code == 200, r.text
    assert r.json()["family"] == "gamma"


def test_analyze_aqua_served_by_gamma_for_parity(client, png_bytes):
    # Aqua normally runs on-device, but an online request must still get a
    # real diagnosis from the base pipeline. The response distinguishes the
    # requested tier from the model that actually produced the probabilities.
    r = _analyze(client, png_bytes, family="aqua")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["family"] == "gamma"
    assert body["requested_family"] == "aqua"
    assert body["family_fallback_reason"] == "aqua_served_by_gamma"
    assert "class_name" in body and "confidence" in body


# ── frontier gating ─────────────────────────────────────────────────


def test_analyze_frontier_disabled_returns_graceful_unavailable(client, png_bytes):
    r = _analyze(client, png_bytes, family="frontier")
    # Mirrors the optional-feature pattern: 200 + available:false, never 5xx.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["family"] == "frontier"
    assert body["reason"] == "frontier_disabled"
    # No diagnosis leaks through the disabled path.
    assert "class_name" not in body


def test_analyze_frontier_enabled_routes_to_frontier_pipeline(
    client, png_bytes, monkeypatch
):
    """After PRO admission, a loaded frontier pipeline serves the request."""
    from tests.conftest import FakePipeline

    async def allow_metered_feature(_request, *, feature):
        assert feature == "frontier_analysis"

    monkeypatch.setattr(
        server_main,
        "_authorize_metered_feature",
        allow_metered_feature,
    )

    frontier = FakePipeline()
    frontier.classifier.model_family = "frontier"
    original = server_main.pipeline_frontier
    server_main.pipeline_frontier = frontier
    try:
        r = _analyze(client, png_bytes, family="frontier")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["family"] == "frontier"
        assert "class_name" in body

        caps = {f["id"]: f for f in client.get("/api/capabilities").json()["families"]}
        assert caps["frontier"]["available"] is True
        assert caps["frontier"]["model_family"] == "frontier"
    finally:
        server_main.pipeline_frontier = original


def test_frontier_and_gamma_caches_do_not_collide(client, png_bytes, monkeypatch):
    """Same bytes, different family → independent cache entries."""
    from tests.conftest import FakePipeline

    authorized: list[str] = []

    async def allow_metered_feature(_request, *, feature):
        authorized.append(feature)

    monkeypatch.setattr(
        server_main,
        "_authorize_metered_feature",
        allow_metered_feature,
    )

    frontier = FakePipeline()
    frontier.classifier.model_family = "frontier"
    original = server_main.pipeline_frontier
    server_main.pipeline_frontier = frontier
    try:
        g = _analyze(client, png_bytes, family="gamma").json()
        f = _analyze(client, png_bytes, family="frontier").json()
        assert g["family"] == "gamma"
        assert f["family"] == "frontier"
        assert authorized == ["gamma_analysis", "frontier_analysis"]
        # A gamma cache hit must never be replayed as frontier.
        assert g.get("is_repeat") is False
        assert f.get("is_repeat") is False
    finally:
        server_main.pipeline_frontier = original
