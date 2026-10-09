"""End-to-end coverage of the public HTTP contract."""

from __future__ import annotations

import hashlib
import io
import sqlite3

from PIL import Image


def test_health_contract(client):
    from classifier import DEFAULT_CLASS_NAMES

    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["num_classes"] == len(DEFAULT_CLASS_NAMES)
    assert body["uptime_seconds"] >= 0


def test_capabilities_model_contract(client):
    # /capabilities is the consolidated metadata endpoint: family availability
    # plus a `model` block and the bilingual `classes` list (folding in what
    # the separate /version and /classes endpoints used to return).
    from classifier import DEFAULT_CLASS_NAMES

    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]
    model = body["model"]
    assert model is not None
    assert model["detector_arch"] == "YOLOv8n (single-class plant_leaf)"
    assert isinstance(model["classifier_arch"], str) and model["classifier_arch"]
    assert model["classifier_name"] == "plantvision"
    assert model["stages"] == 2
    assert model["focus"] == "houseplants + outdoor/garden ornamentals"
    # Capacity tier (aqua/gamma/frontier) declared by the bundle; defaults
    # to "gamma" for bundles trained before the family field existed.
    assert model["model_family"] in {"aqua", "gamma", "frontier"}
    # classes carried inline (the old /classes contract)
    assert len(body["classes"]) == len(DEFAULT_CLASS_NAMES)
    first = body["classes"][0]
    assert set(first.keys()) >= {"class", "name_en", "name_ru", "is_healthy"}
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
        "plantnet",
    ):
        assert key in body
    assert set(body["plantnet"]) == {
        "cache_hits_total",
        "upstream_attempts_total",
        "upstream_retries_total",
        "budget_denials_total",
        "budget_unavailable_total",
        "reserved_cost_micros_total",
        "daily_cost_exposure_micros",
    }
    assert all(type(value) is int for value in body["plantnet"].values())


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
    assert "plantdiseases_plantnet_upstream_attempts_total" in text
    assert "plantdiseases_plantnet_upstream_retries_total" in text
    assert "plantdiseases_plantnet_cache_hits_total" in text
    assert "plantdiseases_plantnet_budget_denials_total" in text
    assert "plantdiseases_plantnet_budget_unavailable_total" in text
    assert "plantdiseases_plantnet_reserved_cost_micros_total" in text
    assert "plantdiseases_plantnet_daily_cost_exposure_micros" in text
    plantnet_samples = [
        line
        for line in text.splitlines()
        if line.startswith("plantdiseases_plantnet_")
    ]
    assert len(plantnet_samples) == 7
    assert all("{" not in line for line in plantnet_samples)


def test_capabilities_int8_flag_surfaced(client):
    """`/api/capabilities` `model.int8_shipped` lets operators see whether the
    deployed `classifier_int8.onnx` is genuine INT8 (true) or the FP32 ONNX
    renamed (false). Read from `models/model_card.json`; absent -> false on a
    fresh checkout.
    """
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    model = r.json()["model"]
    assert "int8_shipped" in model
    assert isinstance(model["int8_shipped"], bool)
    assert "int8_artifact" in model


def test_capabilities_int8_flag_read_from_bundle_analysis_dir(client, tmp_path, monkeypatch):
    """A notebook bundle (§6b) ships model_card.json under analysis/, not at the
    models root; the flag must still reach operators instead of reading false."""
    import json

    import main as server_main

    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "model_card.json").write_text(json.dumps(
        {"int8": {"shipped": True, "shipped_artifact": "classifier_int8.onnx"}}))
    monkeypatch.setattr(server_main, "MODELS_DIR", tmp_path)
    model = client.get("/api/capabilities").json()["model"]
    assert model["int8_shipped"] is True
    assert model["int8_artifact"] == "classifier_int8.onnx"


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
        # ``class_name`` is the probability-backed diagnostic argmax. Explicit
        # abstention fields say whether it may be presented as a diagnosis.
        # The server records it internally as the weak training prior when
        # consent is present; clients do not echo it through the feedback
        # receipt API.
        "class_name",
        "family",
        "requested_family",
        "family_fallback_reason",
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
        "all_probs",
        "top_k",
        "warnings",
        "abstained",
        "abstention_reason",
        "verdict_status",
        "severity",
        "prediction_set",
        "elapsed_ms",
        "server_version",
    }
    missing = required - set(body.keys())
    assert not missing, f"missing keys: {missing}"
    # The canonical class key must match what the localised display
    # strings were derived from. If they ever diverge, the feedback
    # round-trip will pollute per-class accuracy metrics.
    assert isinstance(body["class_name"], str) and body["class_name"]

    assert 0.0 <= body["confidence"] <= 1.0
    assert body["severity"] in ("low", "medium", "high")
    assert isinstance(body["treatment"], list)
    assert isinstance(body["all_probs"], dict)
    assert isinstance(body["top_k"], list)
    assert isinstance(body["warnings"], list)
    assert body["abstained"] is False
    assert body["abstention_reason"] is None
    assert body["verdict_status"] == "accepted"
    # top_k entries are localised — client renders them without a
    # second disease-name lookup.
    for entry in body["top_k"]:
        assert {"class", "confidence", "name_en", "name_ru", "is_healthy"} <= entry.keys()
    assert set(body["detection"].keys()) == {
        "is_diseased", "detector_confidence", "regions", "primary_region",
    }
    assert isinstance(body["detection"]["regions"], list)


def test_ood_warning_becomes_neutral_abstention_without_changing_probabilities(
    client, png_bytes, monkeypatch,
):
    """Adapter regression: old pipeline warnings must fail closed end-to-end."""
    import main as server_main
    from tests.conftest import FakePipeline

    class LegacyOodPipeline(FakePipeline):
        def analyze(self, img):
            result = super().analyze(img)
            probs = {name: 0.0 for name in self.classifier.CLASS_NAMES}
            probs.update({"leaf_spot": 0.70, "healthy": 0.20, "not_a_plant": 0.10})
            result.update({
                "class_name": "leaf_spot",
                "confidence": 0.70,
                "all_probs": probs,
                "top_k": [
                    {"class": "leaf_spot", "confidence": 0.70},
                    {"class": "healthy", "confidence": 0.20},
                    {"class": "not_a_plant", "confidence": 0.10},
                ],
                # No explicit fields: mirrors a cached/pre-contract result.
                "warnings": ["nap_ood_gated"],
                "prediction_set": {
                    "classes": ["leaf_spot"], "coverage": 0.9, "method": "raps",
                },
                "detection": {
                    **result["detection"], "is_diseased": True,
                },
            })
            return result

    monkeypatch.setattr(server_main, "pipeline", LegacyOodPipeline())
    response = client.post(
        "/api/analyze?species=Dieffenbachia",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Diagnostic evidence remains internally coherent and available for audit.
    assert body["class_name"] == "leaf_spot"
    assert body["confidence"] == 0.7
    assert body["all_probs"]["leaf_spot"] == 0.7
    assert body["top_k"][0]["class"] == "leaf_spot"
    # But no disease/species/toxicity/conformal claim escapes the rejection.
    assert body["abstained"] is True
    assert body["abstention_reason"] == "ood"
    assert body["verdict_status"] == "abstained"
    assert body["disease_name"] == "Unidentified Condition"
    assert body["prevention"] == []
    assert body["prediction_set"] is None
    assert body["detection"]["is_diseased"] is False
    assert body["species"] == {
        "available": False,
        "source": "skipped_abstained",
        "best": None,
        "candidates": [],
    }
    assert body["toxic_to_pets"] is False
    assert body["toxic_species_list"] == []
    assert body["toxicity_source"] == "unknown"

    # The cached copy is upgraded by the same legacy-warning derivation.
    repeated = client.post(
        "/api/analyze?species=Dieffenbachia",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    ).json()
    assert repeated["is_repeat"] is True
    assert repeated["abstained"] is True
    assert repeated["abstention_reason"] == "ood"
    assert repeated["toxicity_source"] == "unknown"


def test_natural_nap_keeps_nap_copy_but_is_explicitly_abstained(
    client, png_bytes, monkeypatch,
):
    import main as server_main
    from tests.conftest import FakePipeline

    class NaturalNapPipeline(FakePipeline):
        def analyze(self, img):
            result = super().analyze(img)
            probs = {name: 0.0 for name in self.classifier.CLASS_NAMES}
            probs["not_a_plant"] = 1.0
            result.update({
                "class_name": "not_a_plant",
                "confidence": 1.0,
                "all_probs": probs,
                "top_k": [{"class": "not_a_plant", "confidence": 1.0}],
                "warnings": [],
            })
            return result

    monkeypatch.setattr(server_main, "pipeline", NaturalNapPipeline())
    body = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    ).json()
    assert body["class_name"] == "not_a_plant"
    assert body["disease_name"] != "Unidentified Condition"
    assert body["abstained"] is True
    assert body["abstention_reason"] == "not_a_plant"
    assert body["species"]["source"] == "skipped_nap"


def test_analyze_reports_actual_serving_family_on_aqua_fallback(client, png_bytes):
    response = client.post(
        "/api/analyze?family=aqua",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested_family"] == "aqua"
    assert body["family"] == "gamma"
    assert body["family_fallback_reason"] == "aqua_served_by_gamma"


# ── Species fan-out ─────────────────────────────────────────────────


def test_analyze_response_includes_species_block(client, png_bytes, monkeypatch):
    """Analyze always returns a ``species`` field, even when PlantNet
    is not configured. The block surfaces ``available: false`` so the
    client can render a "species unknown" hint instead of a 404 hop."""
    import main as server_main

    # Replace identify_species with a deterministic offline fake so we
    # don't hit the network from CI. The fake mirrors the no_api_key
    # branch of the real function.
    captured: dict[str, bytes] = {}

    def fake_identify(image_bytes, **_kw):
        captured["image"] = image_bytes
        return {
            "available": False, "results": [], "source": "no_api_key",
        }

    monkeypatch.setattr(server_main, "identify_species", fake_identify)
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
    assert captured["image"] != png_bytes
    with Image.open(io.BytesIO(captured["image"])) as forwarded:
        assert forwarded.format == "JPEG"
        assert max(forwarded.size) <= 1024
        assert not forwarded.getexif()


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


def test_analyze_cache_keys_on_locale_so_species_common_name_localises(
    client, png_bytes, monkeypatch,
):
    """Regression: the same-photo dedup cache used to key on image hash
    alone. PlantNet's ``common_names`` come back in the request's
    ``lang``, so a Russian user's first upload poisoned every later
    English-locale viewer of the same image with Russian names (and
    vice versa). The cache key now includes the resolved PlantNet
    lang, so each locale gets its own cached payload and the species
    block always matches the requesting client's locale.
    """
    import main as server_main

    captured_langs: list[str] = []

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        captured_langs.append(lang)
        common = {
            "ru": "Каучуковое дерево",
            "en": "Rubber tree",
        }.get(lang, "Rubber tree")
        return {
            "available": True, "source": "plantnet",
            "results": [
                {"scientific_name": "Ficus elastica",
                 "common_names": [common], "score": 0.91},
            ],
        }

    monkeypatch.setattr(server_main, "identify_species", fake_identify)

    r_ru = client.post(
        "/api/analyze?locale=ru",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r_ru.status_code == 200, r_ru.text
    body_ru = r_ru.json()
    assert body_ru["is_repeat"] is False
    assert body_ru["species"]["best"]["common_name"] == "Каучуковое дерево"

    # Identical bytes, different locale — must NOT serve the Russian
    # cache entry to an English client.
    r_en = client.post(
        "/api/analyze?locale=en",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r_en.status_code == 200, r_en.text
    body_en = r_en.json()
    assert body_en["species"]["best"]["common_name"] == "Rubber tree"
    assert body_en["is_repeat"] is False  # fresh entry under the en key
    # Same-locale repeat MUST still hit the cache — we didn't sacrifice
    # the dedup feature, just partitioned it by locale.
    r_en2 = client.post(
        "/api/analyze?locale=en",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    body_en2 = r_en2.json()
    assert body_en2["is_repeat"] is True
    assert body_en2["species"]["best"]["common_name"] == "Rubber tree"

    # PlantNet was called once per distinct locale, not three times.
    assert sorted(captured_langs) == ["en", "ru"]


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


def test_identify_endpoint_passes_organ_as_keyword(client, png_bytes, monkeypatch):
    """``identify_species`` declares ``organ`` AND ``lang`` keyword-only.
    The endpoint must call both with keyword args — otherwise the call
    landed via ``run_in_executor(..., organ_value)`` raises
    ``TypeError`` and the route 500s.
    """
    import main as server_main

    captured: dict[str, object] = {}

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        captured["organ"] = organ
        captured["lang"] = lang
        captured["image"] = image_bytes
        return {"available": False, "results": [], "source": "no_api_key"}

    monkeypatch.setattr(server_main, "identify_species", fake_identify)
    r = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        params={"organ": "flower", "locale": "ru"},
    )
    assert r.status_code == 200, r.text
    assert captured["organ"] == "flower"
    assert captured["lang"] == "ru"
    forwarded = captured["image"]
    assert isinstance(forwarded, bytes)
    assert forwarded != png_bytes
    with Image.open(io.BytesIO(forwarded)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1024
        assert not image.getexif()
    assert r.json()["organ"] == "flower"


def test_identify_plantnet_cache_hit_spends_no_user_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import main as server_main

    async def unexpected_authorization(_request, *, feature):
        raise AssertionError(f"cache hit consumed {feature}")

    monkeypatch.setattr(server_main, "plantnet_configured", lambda: True)
    monkeypatch.setattr(
        server_main,
        "plantnet_cache_lookup",
        lambda *_args, **_kwargs: {
            "available": True,
            "results": [
                {
                    "scientific_name": "Ficus elastica",
                    "common_names": ["Rubber tree"],
                    "score": 0.91,
                }
            ],
            "source": "plantnet",
            "cached": True,
            "_upstream_attempted": False,
        },
    )
    monkeypatch.setattr(
        server_main,
        "_authorize_metered_feature",
        unexpected_authorization,
    )
    monkeypatch.setattr(
        server_main,
        "identify_species",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit reached identify_species")
        ),
    )

    response = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["cached"] is True
    assert "_upstream_attempted" not in response.json()


def test_analyze_plantnet_cache_hit_spends_only_diagnosis_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import main as server_main

    authorized = []

    async def record_authorization(_request, *, feature):
        authorized.append(feature)

    monkeypatch.setattr(server_main, "plantnet_configured", lambda: True)
    monkeypatch.setattr(server_main, "_authorize_metered_feature", record_authorization)
    monkeypatch.setattr(
        server_main,
        "plantnet_cache_lookup",
        lambda *_args, **_kwargs: {
            "available": True,
            "results": [
                {
                    "scientific_name": "Ficus elastica",
                    "common_names": ["Rubber tree"],
                    "score": 0.91,
                }
            ],
            "source": "plantnet",
            "cached": True,
            "_upstream_attempted": False,
        },
    )
    monkeypatch.setattr(
        server_main,
        "identify_species",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit reached identify_species")
        ),
    )

    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200, response.text
    assert authorized == ["gamma_analysis"]
    assert response.json()["species"]["best"]["scientific_name"] == (
        "Ficus elastica"
    )


def test_identify_strips_exif_before_plantnet(client, monkeypatch):
    """Direct API clients cannot bypass Android's upload sanitizer."""
    import main as server_main

    sentinel = "SENTINEL-CAMERA-WITH-GPS"
    exif = Image.Exif()
    exif[271] = sentinel
    exif[272] = f"{sentinel}-MODEL"
    raw = io.BytesIO()
    Image.new("RGB", (96, 64), "green").save(raw, format="JPEG", exif=exif)
    original = raw.getvalue()
    captured: dict[str, bytes] = {}

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        captured["image"] = image_bytes
        return {"available": False, "results": [], "source": "no_api_key"}

    monkeypatch.setattr(server_main, "identify_species", fake_identify)
    response = client.post(
        "/api/identify",
        files={"image": ("camera.jpg", original, "image/jpeg")},
    )

    assert response.status_code == 200, response.text
    forwarded = captured["image"]
    assert forwarded != original
    assert sentinel.encode() not in forwarded
    with Image.open(io.BytesIO(forwarded)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert not image.getexif()
        assert {"exif", "icc_profile", "xmp", "iptc"}.isdisjoint(image.info)


def test_identify_endpoint_nap_rejection_skips_plantnet(client, png_bytes, monkeypatch):
    """The local rejection head must stop NAP uploads before they spend
    PlantNet quota or surface a plausible-looking species guess."""
    import main as server_main

    calls = {"pipeline": 0, "plantnet": 0}

    class _NapPipeline:
        def analyze(self, img):
            calls["pipeline"] += 1
            assert img.mode == "RGB"
            return {"class_name": "not_a_plant"}

    def fake_identify(*_args, **_kwargs):
        calls["plantnet"] += 1
        return {"available": True, "results": [], "source": "plantnet"}

    monkeypatch.setattr(server_main, "pipeline", _NapPipeline())
    monkeypatch.setattr(server_main, "identify_species", fake_identify)

    r = client.post(
        "/api/identify",
        files={"image": ("not-a-plant.png", png_bytes, "image/png")},
        params={"organ": "auto", "locale": "en"},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {
        "available": False,
        "results": [],
        "source": "skipped_nap",
        "organ": "auto",
    }
    assert calls == {"pipeline": 1, "plantnet": 0}


def test_identify_endpoint_plant_passes_filter_and_calls_plantnet(
    client, png_bytes, monkeypatch,
):
    """A plant verdict falls through to the existing PlantNet proxy with
    its normalized organ and locale unchanged."""
    import main as server_main

    calls: dict[str, object] = {"pipeline": 0, "plantnet": 0}

    class _PlantPipeline:
        def analyze(self, img):
            calls["pipeline"] = int(calls["pipeline"]) + 1
            calls["image_size"] = img.size
            return {"class_name": "healthy"}

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        calls["plantnet"] = int(calls["plantnet"]) + 1
        calls["organ"] = organ
        calls["lang"] = lang
        calls["bytes_len"] = len(image_bytes)
        return {
            "available": True,
            "results": [
                {
                    "scientific_name": "Ficus elastica",
                    "common_names": ["Каучуковое дерево"],
                    "score": 0.91,
                },
            ],
            "source": "plantnet",
        }

    monkeypatch.setattr(server_main, "pipeline", _PlantPipeline())
    monkeypatch.setattr(server_main, "identify_species", fake_identify)

    r = client.post(
        "/api/identify",
        files={"image": ("plant.png", png_bytes, "image/png")},
        params={"organ": "FLOWER", "locale": "ru-RU"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["organ"] == "flower"
    assert body["results"][0]["scientific_name"] == "Ficus elastica"
    assert {k: calls[k] for k in ("pipeline", "plantnet", "image_size", "organ", "lang")} == {
        "pipeline": 1,
        "plantnet": 1,
        "image_size": (64, 64),
        "organ": "flower",
        "lang": "ru",
    }
    # Forwarded bytes are the metadata-free re-encode, not the upload itself —
    # see test_identify_strips_exif_before_plantnet.
    assert int(calls["bytes_len"]) > 0


def test_analyze_response_issues_separate_community_and_feedback_tokens(
    client, png_bytes
):
    """Publication capabilities must never double as feedback receipts."""
    import community
    import training_store

    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    scan_id = r.json().get("scan_id")
    feedback_receipt = r.json().get("feedback_receipt")
    assert isinstance(scan_id, str) and len(scan_id) >= 16
    assert isinstance(feedback_receipt, str) and len(feedback_receipt) == 43
    assert scan_id != feedback_receipt
    assert community.is_analysis_token_issued(scan_id)
    assert not community.is_analysis_token_issued(feedback_receipt)
    assert training_store.sample_by_receipt(feedback_receipt) is None


def test_analyze_capabilities_bind_exact_upload_on_fresh_and_cached_paths(
    client, png_bytes
):
    import community

    first = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    second = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert first.status_code == second.status_code == 200
    assert second.json()["is_repeat"] is True
    tokens = [first.json()["scan_id"], second.json()["scan_id"]]
    assert all(isinstance(token, str) for token in tokens)

    raw_digest = hashlib.sha256(png_bytes).digest()
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
        rows = connection.execute(
            "SELECT token, image_binding FROM community_analysis_tokens "
            "WHERE token IN (?, ?)",
            tokens,
        ).fetchall()
    bindings = {str(token): str(binding) for token, binding in rows}
    assert set(bindings) == set(tokens)
    for token in tokens:
        expected = hashlib.sha256(token.encode("utf-8") + b"\0" + raw_digest).hexdigest()
        assert bindings[token] == expected
        assert bindings[token] != raw_digest.hex()
    assert bindings[tokens[0]] != bindings[tokens[1]]


def test_analyze_omits_unpersisted_publication_capability_on_cache_miss_and_hit(
    client, png_bytes, monkeypatch,
):
    import main as server_main

    def fail_persistence(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic persistence failure")

    monkeypatch.setattr(server_main, "community_record_analysis_token", fail_persistence)
    first = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    second = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["scan_id"] is None
    assert second.json()["scan_id"] is None
    assert second.json()["is_repeat"] is True
    assert isinstance(first.json()["feedback_receipt"], str)
    assert isinstance(second.json()["feedback_receipt"], str)


def test_feedback_rejects_legacy_scan_id_payload(client, png_bytes):
    """Identifier-rich legacy payloads are rejected, not silently ignored."""
    r = client.post(
        "/api/feedback",
        json={"scan_id": "neverissuedXYZ", "correct": True},
    )
    assert r.status_code == 422, r.text
    errors = r.json()["detail"]
    assert any(error["loc"][-1] == "scan_id" for error in errors)
