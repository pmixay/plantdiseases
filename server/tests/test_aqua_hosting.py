"""Hosted Aqua on-device model: manifest + model-bytes endpoints.

The Aqua TFLite student is served to the app so it can download + cache it and
pick up updates, instead of bundling it in the APK. The model is provisioned
out of band (never committed), so these tests stub a fake artefact dir.
"""

from __future__ import annotations

import hashlib
import json

import main as server_main


def test_aqua_manifest_unavailable_when_not_provisioned(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    r = client.get("/api/aqua/manifest")
    assert r.status_code == 200, r.text
    assert r.json() == {"available": False}


def test_aqua_model_404_when_not_provisioned(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    assert client.get("/api/aqua/model").status_code == 404


def test_aqua_manifest_and_model_when_provisioned(client, monkeypatch, tmp_path):
    blob = b"FAKE_TFLITE_BYTES_FOR_TEST" * 16
    (tmp_path / "aqua_classifier.tflite").write_bytes(blob)
    (tmp_path / "classes.json").write_text(json.dumps({
        "model_version": "2.4.0",
        "model_family": "aqua",
        "architecture": "mobilenetv3_small",
        "class_names": ["healthy", "leaf_spot"],
        "training": {"img_size": 224},
        "inference": {"normalize_mean": [0.485, 0.456, 0.406]},
    }))
    monkeypatch.setattr(server_main, "AQUA_STATIC_DIR", tmp_path)
    server_main._aqua_sha_cache.clear()

    m = client.get("/api/aqua/manifest").json()
    assert m["available"] is True
    assert m["version"] == "2.4.0"
    assert m["size_bytes"] == len(blob)
    assert m["sha256"] == hashlib.sha256(blob).hexdigest()
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
