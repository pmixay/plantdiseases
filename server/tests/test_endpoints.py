"""End-to-end coverage of the public HTTP contract."""

from __future__ import annotations


def test_health_contract(client):
    from classifier import DEFAULT_CLASS_NAMES

    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["num_classes"] == len(DEFAULT_CLASS_NAMES)
    assert body["uptime_seconds"] >= 0


def test_version_contract(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["detector_arch"] == "YOLOv8n (single-class plant_leaf, indoor-trained)"
    assert isinstance(body["classifier_arch"], str) and body["classifier_arch"]
    assert body["classifier_name"] == "plantvision"
    assert body["stages"] == 2
    assert body["focus"] == "houseplants"


def test_classes_contract(client):
    from classifier import DEFAULT_CLASS_NAMES

    r = client.get("/api/classes")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(DEFAULT_CLASS_NAMES)
    assert len(body["classes"]) == len(DEFAULT_CLASS_NAMES)

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
        "ai_doctor",
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
    assert "plantdiseases_build_info" in text


def test_analyze_int8_flag_surfaced(client):
    """`/api/version` must expose `int8_shipped` so operators can see
    that the deployed `classifier_int8.onnx` is genuine INT8 (true) or
    the FP32 ONNX renamed (false). The flag is read from
    `models/model_card.json` at request time; it is OK for it to be
    absent (-> false) on a fresh checkout.
    """
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert "int8_shipped" in body
    assert isinstance(body["int8_shipped"], bool)
    assert "int8_artifact" in body


def test_analyze_inference_timeout_504(client, png_bytes):
    """A pipeline that takes longer than `INFERENCE_TIMEOUT` must yield
    a 504 (asyncio.wait_for path). We patch the live `pipeline.analyze`
    to block past the timeout and temporarily shrink `INFERENCE_TIMEOUT`
    so the test runs in well under a second.
    """
    import time

    import main as server_main

    real_pipeline = server_main.pipeline
    real_timeout = server_main.INFERENCE_TIMEOUT
    original_analyze = real_pipeline.analyze

    def _slow_analyze(img):  # runs in the inference threadpool
        time.sleep(0.5)
        return original_analyze(img)

    real_pipeline.analyze = _slow_analyze
    server_main.INFERENCE_TIMEOUT = 0.05
    try:
        r = client.post(
            "/api/analyze",
            files={"image": ("leaf.png", png_bytes, "image/png")},
        )
        assert r.status_code == 504, r.text
        assert "timed out" in r.json()["detail"].lower()
    finally:
        real_pipeline.analyze = original_analyze
        server_main.INFERENCE_TIMEOUT = real_timeout


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
        "top_k",
        "warnings",
        "severity",
        "prediction_set",
        "elapsed_ms",
        "server_version",
    }
    missing = required - set(body.keys())
    assert not missing, f"missing keys: {missing}"

    assert 0.0 <= body["confidence"] <= 1.0
    assert 0.0 <= body["uncertainty"] <= 1.0
    assert body["severity"] in ("low", "medium", "high")
    assert isinstance(body["treatment"], list)
    assert isinstance(body["all_probs"], dict)
    assert isinstance(body["top_k"], list)
    assert isinstance(body["warnings"], list)
    # top_k entries are localised — client renders them without a
    # second disease-name lookup.
    for entry in body["top_k"]:
        assert {"class", "confidence", "name_en", "name_ru", "is_healthy"} <= entry.keys()
    assert set(body["detection"].keys()) == {
        "is_diseased", "detector_confidence", "regions", "primary_region",
    }
    assert isinstance(body["detection"]["regions"], list)


# ── Species fan-out ─────────────────────────────────────────────────


def test_analyze_response_includes_species_block(client, png_bytes, monkeypatch):
    """Analyze always returns a ``species`` field, even when PlantNet
    is not configured. The block surfaces ``available: false`` so the
    client can render a "species unknown" hint instead of a 404 hop."""
    import main as server_main

    # Replace identify_species with a deterministic offline fake so we
    # don't hit the network from CI. The fake mirrors the no_api_key
    # branch of the real function.
    monkeypatch.setattr(
        server_main, "identify_species",
        lambda *_args, **_kw: {
            "available": False, "results": [], "source": "no_api_key",
        },
    )
    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "species" in body, "analyze must always surface a species block"
    species = body["species"]
    assert species["available"] is False
    assert species["source"] == "no_api_key"
    assert species["best"] is None


def test_analyze_species_block_includes_best_match(client, png_bytes, monkeypatch):
    """When PlantNet returns candidates, the analyze response copies
    the top match into ``species.best`` and the next two into
    ``species.candidates``."""
    import main as server_main

    monkeypatch.setattr(
        server_main, "identify_species",
        lambda *_args, **_kw: {
            "available": True, "source": "plantnet",
            "results": [
                {"scientific_name": "Begonia rex", "common_names": ["Painted-Leaf Begonia"], "score": 0.82},
                {"scientific_name": "Begonia maculata", "common_names": ["Polka Dot Begonia"], "score": 0.10},
                {"scientific_name": "Pilea peperomioides", "common_names": ["Chinese Money"], "score": 0.04},
            ],
        },
    )
    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    body = r.json()
    species = body["species"]
    assert species["available"] is True
    assert species["best"]["scientific_name"] == "Begonia rex"
    assert species["best"]["common_name"] == "Painted-Leaf Begonia"
    assert len(species["candidates"]) == 3


def test_analyze_species_block_survives_plantnet_failure(client, png_bytes, monkeypatch):
    """If identify_species raises, the analyze response still
    completes — ``species`` lands as ``None`` rather than 500-ing the
    whole request.
    """
    import main as server_main

    def explode(*_a, **_kw):
        raise RuntimeError("upstream is on fire")

    monkeypatch.setattr(server_main, "identify_species", explode)
    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["species"] is None
