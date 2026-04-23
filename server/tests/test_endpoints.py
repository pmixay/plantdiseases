"""End-to-end coverage of the public HTTP contract."""

from __future__ import annotations


def test_health_contract(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["pipeline_mode"] in ("full", "partial", "demo")
    assert body["num_classes"] == 15
    assert body["uptime_seconds"] >= 0


def test_version_contract(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["detector_arch"] == "MobileNetV3-Small"
    assert body["classifier_arch"] == "EfficientNet-B0"
    assert body["stages"] == 2
    assert body["pipeline_mode"] in ("full", "partial", "demo")


def test_classes_contract(client):
    r = client.get("/api/classes")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 15
    assert len(body["classes"]) == 15

    first = body["classes"][0]
    assert set(first.keys()) >= {"class", "name_en", "name_ru", "is_healthy"}
    # "healthy" class must be present and flagged.
    healthy = [c for c in body["classes"] if c["class"] == "healthy"]
    assert len(healthy) == 1
    assert healthy[0]["is_healthy"] is True


def test_metrics_json(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "uptime_seconds",
        "total_requests",
        "total_analyses",
        "total_errors",
        "avg_latency_ms",
        "class_counts",
        "pipeline_mode",
    ):
        assert key in body


def test_metrics_prometheus_format(client):
    r = client.get("/api/metrics/prometheus")
    assert r.status_code == 200
    ctype = r.headers.get("content-type", "")
    assert ctype.startswith("text/plain")
    assert "version=0.0.4" in ctype

    text = r.text
    assert "# HELP plantdiseases_uptime_seconds" in text
    assert "# TYPE plantdiseases_uptime_seconds gauge" in text
    assert "plantdiseases_requests_total" in text
    assert "plantdiseases_analyses_total" in text
    assert "plantdiseases_pipeline_mode_info" in text


def test_analyze_response_contract(client, png_bytes):
    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    required = {
        "disease_name",
        "disease_name_ru",
        "confidence",
        "description",
        "description_ru",
        "treatment",
        "treatment_ru",
        "prevention",
        "prevention_ru",
        "is_healthy",
        "detection",
        "uncertainty",
        "all_probs",
        "pipeline_mode",
        "elapsed_ms",
        "server_version",
    }
    missing = required - set(body.keys())
    assert not missing, f"missing keys: {missing}"

    assert 0.0 <= body["confidence"] <= 1.0
    assert 0.0 <= body["uncertainty"] <= 1.0
    assert isinstance(body["treatment"], list)
    assert isinstance(body["all_probs"], dict)
    assert set(body["detection"].keys()) == {"is_diseased", "detector_confidence", "region"}
