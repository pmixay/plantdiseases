"""Coverage for the /api/progression endpoint extended with a history list."""

from __future__ import annotations


def _scan(cls: str, conf: float, sev: str = "low") -> dict:
    return {
        "class_name": cls,
        "disease_name": cls.replace("_", " ").title(),
        "confidence": conf,
        "severity": sev,
    }


def test_progression_history_baseline_on_empty(client):
    r = client.post("/api/progression", json={"history": []})
    assert r.status_code == 200
    body = r.json()
    assert body["trend"] == "baseline"
    assert body["history_length"] == 0
    assert isinstance(body["sparkline"], list) and len(body["sparkline"]) == 5


def test_progression_history_single_point(client):
    r = client.post(
        "/api/progression",
        json={"history": [_scan("healthy", 0.9)]},
    )
    body = r.json()
    assert body["history_length"] == 1
    assert body["current_class"] == "healthy"
    assert body["previous_class"] is None
    assert len(body["sparkline"]) == 5
    # Healthy ⇒ flat-zero sparkline.
    assert all(p == 0.0 for p in body["sparkline"])


def test_progression_history_worsening_trend(client):
    """A monotonically worsening series of scans must yield a worsening
    trend AND a non-decreasing sparkline."""
    history = [
        _scan("leaf_spot_fungal", 0.4, "low"),
        _scan("leaf_spot_fungal", 0.6, "medium"),
        _scan("leaf_spot_fungal", 0.8, "medium"),
        _scan("leaf_spot_fungal", 0.9, "high"),
    ]
    r = client.post("/api/progression", json={"history": history})
    body = r.json()
    assert body["trend"] == "worsening"
    assert body["history_length"] == 4
    # Sparkline should be non-decreasing (allow small numerical wobble).
    sparkline = body["sparkline"]
    for a, b in zip(sparkline, sparkline[1:]):
        assert b + 1e-6 >= a


def test_progression_history_improving_trend(client):
    history = [
        _scan("viral_disease", 0.95, "high"),
        _scan("viral_disease", 0.85, "medium"),
        _scan("leaf_spot_fungal", 0.65, "medium"),
        _scan("healthy", 0.9),
    ]
    r = client.post("/api/progression", json={"history": history})
    body = r.json()
    assert body["trend"] == "improving"
    assert body["sparkline"][-1] < body["sparkline"][0]


def test_progression_rejects_payload_without_history(client):
    """The endpoint requires the ``history`` field. Pydantic surfaces
    the missing field as a 422 Unprocessable Entity."""
    r = client.post("/api/progression", json={})
    assert r.status_code == 422
