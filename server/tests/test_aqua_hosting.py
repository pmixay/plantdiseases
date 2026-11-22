"""Hosted Aqua on-device model: manifest + model-bytes endpoints.

The Aqua TFLite student is served to the app so it can download + cache it and
pick up updates, instead of bundling it in the APK. The model is provisioned
out of band (never committed), so these tests stub a fake artefact dir.
"""

from __future__ import annotations

import hashlib
import json

import main as server_main


def _valid_classes() -> dict:
    return {
        "model_version": "2.5.0",
        "model_family": "aqua",
        "architecture": "mobilenetv3_small",
        "class_names": ["healthy", "leaf_spot"],
        "training": {"img_size": 224, "eval_resize_ratio": 1.05},
        "inference": {
            "layout": "NHWC",
            "tta": "none",
            "output_semantics": "logits",
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
        },
    }


def test_aqua_manifest_unavailable_when_not_provisioned(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    r = client.get("/api/aqua/manifest")
    assert r.status_code == 200, r.text
    assert r.json() == {"available": False}


def test_aqua_model_404_when_not_provisioned(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    assert client.get("/api/aqua/model").status_code == 404


def test_aqua_manifest_and_model_when_provisioned(client, monkeypatch, tmp_path):
    import routes_content
    blob = b"FAKE_TFLITE_BYTES_FOR_TEST" * 16
    (tmp_path / "aqua_classifier.tflite").write_bytes(blob)
    classes = _valid_classes()
    (tmp_path / "classes.json").write_text(json.dumps(classes))
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    routes_content._aqua_model_sha256.cache_clear()

    m = client.get("/api/aqua/manifest").json()
    assert m["available"] is True
    assert m["version"] == "2.5.0"
    assert m["size_bytes"] == len(blob)
    assert m["sha256"] == hashlib.sha256(blob).hexdigest()
    classes_sha = routes_content._aqua_classes_sha256(classes)
    assert m["classes_sha256"] == classes_sha
    assert m["bundle_sha256"] == routes_content._aqua_bundle_sha256(m["sha256"], classes_sha)
    assert m["bundle_schema_version"] == 1
    # The sidecar metadata the app needs to run the model rides along.
    assert m["classes"]["class_names"] == ["healthy", "leaf_spot"]
    assert m["classes"]["model_family"] == "aqua"

    r = client.get("/api/aqua/model")
    assert r.status_code == 200
    assert r.content == blob
    assert r.headers["content-type"] == "application/octet-stream"


def test_aqua_manifest_unavailable_when_sidecar_missing(client, monkeypatch, tmp_path):
    # Model present but no classes.json ⇒ unusable ⇒ report unavailable.
    (tmp_path / "aqua_classifier.tflite").write_bytes(b"x")
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    assert client.get("/api/aqua/manifest").json() == {"available": False}


def test_aqua_manifest_rejects_sidecar_the_client_cannot_install(
    client, monkeypatch, tmp_path,
):
    import routes_content
    (tmp_path / "aqua_classifier.tflite").write_bytes(b"model")
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    routes_content._aqua_model_sha256.cache_clear()

    for mutation in ("resize", "semantics", "normalization", "family", "reserved"):
        classes = _valid_classes()
        if mutation == "resize":
            del classes["training"]["eval_resize_ratio"]
        elif mutation == "semantics":
            del classes["inference"]["output_semantics"]
        elif mutation == "normalization":
            classes["inference"]["normalize_std"] = [0.229, 0.0, 0.225]
        elif mutation == "family":
            classes["model_family"] = "gamma"
        else:
            classes["_model_sha256"] = "0" * 64
        (tmp_path / "classes.json").write_text(json.dumps(classes))
        assert client.get("/api/aqua/manifest").json() == {"available": False}
