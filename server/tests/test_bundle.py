"""Checksummed immutable serving-bundle contract tests (no model execution)."""

from __future__ import annotations

import json

import pytest

from bundle import BundleLoadError, _validate_served_gate, verify_bundle
from tests.bundle_fixtures import record as _record
from tests.bundle_fixtures import write_bundle as _bundle
from tests.bundle_fixtures import write_manifest as _write_manifest
from tools import verify_bundle as verify_bundle_cli


def test_verify_bundle_returns_only_manifest_selected_graphs(tmp_path):
    _bundle(tmp_path)
    verified = verify_bundle(tmp_path)
    assert verified.classifier_path.name == "classifier_int8.onnx"
    assert verified.detector_path.name == "detector.onnx"
    assert len(verified.manifest_sha256) == 64
    assert len(verified.serving_contract_sha256) == 64


def test_verify_bundle_rejects_tamper_and_stale_sibling(tmp_path):
    _bundle(tmp_path)
    (tmp_path / "classifier_int8.onnx").write_bytes(b"changed")
    with pytest.raises(BundleLoadError, match="size mismatch|SHA-256 mismatch"):
        verify_bundle(tmp_path)

    _bundle(tmp_path)
    (tmp_path / "classifier.onnx").write_bytes(b"stale fallback")
    with pytest.raises(BundleLoadError, match="unbound"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_cross_run_calibration(tmp_path):
    manifest = _bundle(tmp_path)
    (tmp_path / "temperature.json").write_text(json.dumps({
        "temperature": 1.2, "source": "fp32_fallback",
    }))
    manifest["artifacts"]["temperature"] = _record(tmp_path / "temperature.json")
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="not fit"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_missing_or_escaping_manifest(tmp_path):
    with pytest.raises(BundleLoadError, match="manifest is missing"):
        verify_bundle(tmp_path)

    manifest = _bundle(tmp_path)
    manifest["artifacts"]["classifier"]["path"] = "../classifier_int8.onnx"
    manifest["classifier_selection"]["path"] = "../classifier_int8.onnx"
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="unsafe path"):
        verify_bundle(tmp_path)


def test_verify_bundle_requires_explicit_optional_artifact_roles(tmp_path):
    manifest = _bundle(tmp_path)
    del manifest["artifacts"]["conformal"]
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="explicitly declare"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_metadata_that_would_use_runtime_defaults(tmp_path):
    manifest = _bundle(tmp_path)
    classes_path = tmp_path / "classes.json"
    classes = json.loads(classes_path.read_text())
    del classes["inference"]["normalize_mean"]
    classes_path.write_text(json.dumps(classes))
    manifest["artifacts"]["classes"] = _record(classes_path)
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="normalize_mean"):
        verify_bundle(tmp_path)


def test_verify_bundle_cli_is_inference_free_preflight(tmp_path, capsys):
    manifest = _bundle(tmp_path)
    assert verify_bundle_cli.main([str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["bundle_id"] == manifest["bundle_id"]
    assert payload["classifier"] == "classifier_int8.onnx"

    (tmp_path / "classes.json").write_text("{}")
    assert verify_bundle_cli.main([str(tmp_path)]) == 1
    assert "INVALID:" in capsys.readouterr().err


def test_verify_bundle_rejects_stub_serving_contract(tmp_path):
    manifest = _bundle(tmp_path)
    manifest["serving_contract"] = {"preprocessing": {"output_semantics": "logits"}}
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="schema_version|missing sections"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_embedding_without_explicit_bank_file(tmp_path):
    """ood.py defaults a missing bank_file to ood_embedding.npz; a bundle that
    passed preflight and then failed at boot is the worst preflight outcome."""
    manifest = _bundle(tmp_path)
    classes_path = tmp_path / "classes.json"
    classes = json.loads(classes_path.read_text())
    classes["ood"]["embedding"] = {
        "method": "knn", "metric": "cosine", "k": 5,
        "threshold": 0.5, "embed_dim": 384,
    }
    classes_path.write_text(json.dumps(classes))
    manifest["artifacts"]["classes"] = _record(classes_path)
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="bank_file"):
        verify_bundle(tmp_path)


def test_served_gate_sidecar_validation():
    """``ood.served_gate`` is null (overlay off) or an object with at least one
    finite served threshold; a gate that names none is an enabled no-op and is
    refused at preflight, as are out-of-range values."""
    _validate_served_gate(None)
    _validate_served_gate({"embedding_threshold": 0.63, "max_logit_floor": None})
    _validate_served_gate({"embedding_threshold": None, "max_logit_floor": 1.75})
    for bad in (
        "off",
        {"embedding_threshold": None, "max_logit_floor": None},
        {"embedding_threshold": 2.5},
        {"embedding_threshold": -0.1},
        {"embedding_threshold": True},
        {"max_logit_floor": float("nan")},
        {"max_logit_floor": "1.7"},
    ):
        with pytest.raises(BundleLoadError):
            _validate_served_gate(bad)
