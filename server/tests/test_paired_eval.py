"""Tests for the paired A/B eval harness (``tools/paired_eval.py``).

The join and the filter are the whole trust surface here: if a supplement row
fails to match the file it became, it is silently dropped from the comparison;
if a non-supplement row survives the filter, the incumbent gets scored on
images it trained on. Both failures produce a plausible-looking number, so
they are tested directly rather than eyeballed.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from tests.bundle_fixtures import (  # noqa: E402
    record,
    write_bundle,
    write_manifest,
)
from tools.paired_eval import (  # noqa: E402
    ABSTAIN_SENTINEL,
    _load_bundle_binding,
    _manifest_capture_families,
    _manifest_provenance,
    _sanitise_stem,
    _served_class_or_abstain,
    _shared_class_names,
    _stratified_limit,
    collect_eval_items,
)

CLASSES = ["gray_mold_botrytis", "healthy", "leaf_scorch_underwatering",
           "leaf_spot", "not_a_plant", "nutrient_deficiency", "pests",
           "powdery_mildew", "viral_disease"]


def _write_manifest(path: Path, rows: list[dict]) -> None:
    cols = ["filename", "class", "provenance"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _touch(root: Path, cls: str, stem: str) -> Path:
    d = root / cls
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stem}.jpg"
    p.write_bytes(b"not-a-real-jpeg")   # collect_eval_items never decodes
    return p


def test_sanitise_matches_notebook_rule():
    # The notebook does re.sub(r'[^A-Za-z0-9_.-]', '_', ...) on the joined stem.
    assert _sanitise_stem("supp_leaf_spot_ls 0007") == "supp_leaf_spot_ls_0007"
    assert _sanitise_stem("supp_healthy_a/b:c") == "supp_healthy_a_b_c"
    assert _sanitise_stem("supp_pests_ok-1.2") == "supp_pests_ok-1.2"


def test_manifest_provenance_keys_on_the_on_disk_stem(tmp_path):
    _write_manifest(tmp_path / "m.csv", [
        {"filename": "leaf_spot/ls 0007.jpg", "class": "leaf_spot", "provenance": "new"},
        {"filename": "healthy/h_1.jpg", "class": "healthy", "provenance": "carried_from_v5"},
    ])
    prov = _manifest_provenance(tmp_path / "m.csv")
    # Key is what the notebook WOULD have written to disk, not the manifest path.
    assert prov["supp_leaf_spot_ls_0007"] == "new"
    assert prov["supp_healthy_h_1"] == "carried_from_v5"


def test_manifest_without_provenance_column_refuses(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("filename,class\nleaf_spot/a.jpg,leaf_spot\n", encoding="utf-8")
    try:
        _manifest_provenance(p)
    except SystemExit as e:
        assert "provenance" in str(e)
    else:
        raise AssertionError("a manifest with no provenance column must refuse, not guess")


def test_filter_keeps_only_non_recycled_supplement_rows(tmp_path):
    """The core guarantee: recycled rows and non-supplement rows are dropped.

    `carried_from_v5` sits inside the incumbent's own training set, and a
    PlantDoc/iNat val image has no provenance at all — scoring either one
    flatters the incumbent.
    """
    _write_manifest(tmp_path / "m.csv", [
        {"filename": "leaf_spot/new1.jpg", "class": "leaf_spot", "provenance": "new"},
        {"filename": "leaf_spot/v7.jpg", "class": "leaf_spot", "provenance": "carried_from_v7"},
        {"filename": "leaf_spot/old.jpg", "class": "leaf_spot", "provenance": "carried_from_v5"},
    ])
    prov = _manifest_provenance(tmp_path / "m.csv")
    ev = tmp_path / "val"
    _touch(ev, "leaf_spot", "supp_leaf_spot_new1")     # keep
    _touch(ev, "leaf_spot", "supp_leaf_spot_v7")       # keep
    _touch(ev, "leaf_spot", "supp_leaf_spot_old")      # drop: incumbent trained on it
    _touch(ev, "leaf_spot", "plantdoc_1234")           # drop: not a supplement row
    _touch(ev, "healthy", "inat_99")                   # drop: not a supplement row

    paths, y_true, stats = collect_eval_items(
        ev, CLASSES, {"new", "carried_from_v7", "carried_from_v8", "carried_from_v8b"}, prov)

    assert stats["kept"] == 2
    assert stats["dropped_provenance"] == 1
    assert stats["dropped_not_supplement"] == 2
    assert sorted(p.stem for p in paths) == ["supp_leaf_spot_new1", "supp_leaf_spot_v7"]
    assert set(y_true) == {CLASSES.index("leaf_spot")}
    assert dict(stats["kept_by_provenance"]) == {"new": 1, "carried_from_v7": 1}


def test_no_filter_keeps_everything_and_labels_by_folder(tmp_path):
    ev = tmp_path / "val"
    _touch(ev, "healthy", "a")
    _touch(ev, "pests", "b")
    _touch(ev, "pests", "c")
    paths, y_true, stats = collect_eval_items(ev, CLASSES, None, None)
    assert stats["kept"] == 3 and len(paths) == 3
    assert sorted(y_true) == sorted(
        [CLASSES.index("healthy"), CLASSES.index("pests"), CLASSES.index("pests")])


def test_unknown_class_folder_is_dropped_not_mislabelled(tmp_path):
    """A folder outside the shared class space must not be silently remapped."""
    ev = tmp_path / "val"
    _touch(ev, "healthy", "a")
    _touch(ev, "edema", "b")          # deferred class: in no shipped head
    _, y_true, stats = collect_eval_items(ev, CLASSES, None, None)
    assert stats["kept"] == 1
    assert stats["dropped_unknown_class"] == 1
    assert y_true == [CLASSES.index("healthy")]


def test_nested_manifest_path_joins_by_complete_class_path(tmp_path):
    _write_manifest(tmp_path / "m.csv", [
        {"filename": "leaf_spot/session_a/x.jpg", "class": "leaf_spot",
         "provenance": "new"},
        {"filename": "leaf_spot/session_b/x.jpg", "class": "leaf_spot",
         "provenance": "carried_from_v7"},
    ])
    prov = _manifest_provenance(tmp_path / "m.csv")
    assert prov["leaf_spot/session_a/x"] == "new"
    assert prov["leaf_spot/session_b/x"] == "carried_from_v7"
    assert prov["supp_leaf_spot_session_a_x"] == "new"

    ev = tmp_path / "raw"
    _touch(ev / "leaf_spot", "session_a", "x")
    _touch(ev / "leaf_spot", "session_b", "x")
    paths, _, stats = collect_eval_items(ev, CLASSES, {"new"}, prov)
    assert [p.relative_to(ev).as_posix() for p in paths] == [
        "leaf_spot/session_a/x.jpg"]
    assert stats["dropped_provenance"] == 1


def test_manifest_rejects_sanitised_disk_stem_collision(tmp_path):
    _write_manifest(tmp_path / "m.csv", [
        {"filename": "leaf_spot/a b/x.jpg", "class": "leaf_spot", "provenance": "new"},
        {"filename": "leaf_spot/a_b/x.jpg", "class": "leaf_spot", "provenance": "new"},
    ])
    with pytest.raises(SystemExit, match="collides"):
        _manifest_provenance(tmp_path / "m.csv")


def test_class_contract_is_strict_by_default():
    with pytest.raises(ValueError, match="class sets differ"):
        _shared_class_names(["a", "b"], ["a", "c"])
    assert _shared_class_names(["a", "b"], ["a", "c"], True) == ["a"]


def test_limit_is_class_round_robin_not_sorted_prefix(tmp_path):
    paths = [tmp_path / f"p{i}.jpg" for i in range(8)]
    y = [0, 0, 0, 0, 1, 1, 2, 2]
    limited_paths, limited_y = _stratified_limit(paths, y, 6)
    assert limited_y == [0, 1, 2, 0, 1, 2]
    assert len(limited_paths) == 6


def test_served_rejection_warnings_are_abstentions_not_raw_argmax():
    assert _served_class_or_abstain({
        "class_name": "leaf_spot", "warnings": ["nap_ood_gated"]}) == ABSTAIN_SENTINEL
    assert _served_class_or_abstain({
        "class_name": "healthy", "warnings": [{"code": "blurry_image"}]}) == ABSTAIN_SENTINEL
    assert _served_class_or_abstain({
        "class_name": "leaf_spot", "warnings": ["low_confidence"]}) == "leaf_spot"


def test_manifest_capture_family_uses_full_path_keys(tmp_path):
    path = tmp_path / "m.csv"
    cols = ["filename", "class", "provenance", "capture_family_id"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"filename": "leaf_spot/a/x.jpg", "class": "leaf_spot",
                    "provenance": "new", "capture_family_id": "family-a"})
        w.writerow({"filename": "leaf_spot/b/x.jpg", "class": "leaf_spot",
                    "provenance": "new", "capture_family_id": "family-b"})
    groups = _manifest_capture_families(path)
    assert groups["leaf_spot/a/x"] == "family-a"
    assert groups["leaf_spot/b/x"] == "family-b"


def test_first_seen_version_excludes_rows_seen_by_either_model_era(tmp_path):
    path = tmp_path / "m.csv"
    cols = ["filename", "class", "provenance", "first_seen_model_version"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for stem, first_seen in (("old", "2.6.8"), ("future", "2.8.0"),
                                 ("unseen", "never")):
            w.writerow({"filename": f"leaf_spot/{stem}.jpg", "class": "leaf_spot",
                        "provenance": "new", "first_seen_model_version": first_seen})
    prov = _manifest_provenance(path, eval_through_version="2.7.3")
    assert "leaf_spot/old" not in prov
    assert prov["leaf_spot/future"] == "new"
    assert prov["leaf_spot/unseen"] == "new"


def test_bundle_binding_selects_and_verifies_one_exact_graph(tmp_path):
    manifest = write_bundle(tmp_path)
    binding = _load_bundle_binding(tmp_path)
    assert binding["bound"] is True
    assert binding["classifier_path"] == (tmp_path / "classifier_int8.onnx").resolve()
    assert binding["selected_classifier"] == "classifier_int8.onnx"
    assert binding["bundle_id"] == manifest["bundle_id"]
    assert binding["run_id"] == manifest["run_id"]
    assert binding["model_version"] == manifest["model_version"]
    assert binding["dataset_sha256"] == "d" * 64
    assert len(binding["bundle_manifest_sha256"]) == 64
    assert len(binding["serving_contract_sha256"]) == 64


def test_bundle_binding_rejects_tampered_artifact(tmp_path):
    write_bundle(tmp_path)
    (tmp_path / "temperature.json").write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="size mismatch|SHA-256 mismatch"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_unbound_sibling_classifier(tmp_path):
    write_bundle(tmp_path)
    (tmp_path / "classifier.onnx").write_bytes(b"stale sibling")
    with pytest.raises(SystemExit, match="unbound"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_any_other_root_classifier_graph(tmp_path):
    """Stricter than boot: boot only knows the two serving names, but an A/B
    must not score a bundle whose root holds a second graph of any name."""
    write_bundle(tmp_path)
    (tmp_path / "classifier_old.onnx").write_bytes(b"leftover experiment")
    with pytest.raises(SystemExit, match="expected exactly"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_zero_byte_artifact(tmp_path):
    """An empty file hashes to a valid SHA-256; boot refuses size 0, so must we."""
    manifest = write_bundle(tmp_path)
    (tmp_path / "conformal.json").write_bytes(b"")
    manifest["artifacts"]["conformal"] = record(tmp_path / "conformal.json")
    write_manifest(tmp_path, manifest)
    with pytest.raises(SystemExit, match="invalid byte size"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_symlink_out_of_the_bundle(tmp_path):
    """A bound path must resolve inside the bundle, not follow a link elsewhere."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    manifest = write_bundle(bundle_dir)
    outside = tmp_path / "elsewhere.onnx"
    outside.write_bytes((bundle_dir / "detector.onnx").read_bytes())
    (bundle_dir / "detector.onnx").unlink()
    (bundle_dir / "detector.onnx").symlink_to(outside)
    write_manifest(bundle_dir, manifest)
    with pytest.raises(SystemExit, match="escapes the bundle"):
        _load_bundle_binding(bundle_dir)


def test_bundle_binding_rejects_calibration_fit_for_another_graph(tmp_path):
    manifest = write_bundle(tmp_path)
    (tmp_path / "temperature.json").write_text(json.dumps({
        "temperature": 1.2, "source": "fp32",
    }))
    manifest["artifacts"]["temperature"] = record(tmp_path / "temperature.json")
    write_manifest(tmp_path, manifest)
    with pytest.raises(SystemExit, match="not fit for the selected classifier"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_classes_contract_boot_would_refuse(tmp_path):
    manifest = write_bundle(tmp_path)
    classes_path = tmp_path / "classes.json"
    classes = json.loads(classes_path.read_text())
    classes["run_id"] = "another-run"
    classes_path.write_text(json.dumps(classes))
    manifest["artifacts"]["classes"] = record(classes_path)
    write_manifest(tmp_path, manifest)
    with pytest.raises(SystemExit, match="run_id does not match classes.json"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_stub_serving_contract(tmp_path):
    manifest = write_bundle(tmp_path)
    manifest["serving_contract"] = {
        "schema_version": 1, "detector": {}, "crop": {}, "classifier": {},
        "blur": {}, "ood": {},
    }
    write_manifest(tmp_path, manifest)
    with pytest.raises(SystemExit, match="serving_contract.detector is missing"):
        _load_bundle_binding(tmp_path)


def test_bundle_binding_rejects_run_manifest_from_another_run(tmp_path):
    manifest = write_bundle(tmp_path)
    run_path = tmp_path / "run_manifest.json"
    run = json.loads(run_path.read_text())
    run["model_version"] = "8.9.9"
    run_path.write_text(json.dumps(run))
    manifest["artifacts"]["run_manifest"] = record(run_path)
    write_manifest(tmp_path, manifest)
    with pytest.raises(SystemExit, match="run_manifest.json -> model_version"):
        _load_bundle_binding(tmp_path)


def test_unbound_bundle_requires_explicit_diagnostic_opt_in(tmp_path):
    (tmp_path / "classifier.onnx").write_bytes(b"legacy")
    (tmp_path / "classes.json").write_text('{"class_names":[]}', encoding="utf-8")
    with pytest.raises(SystemExit, match="bundle_manifest.json is required"):
        _load_bundle_binding(tmp_path)
    binding = _load_bundle_binding(tmp_path, allow_legacy_unbound=True)
    assert binding["bound"] is False
    assert "not release evidence" in binding["diagnostic_advisory"]


def test_served_verdict_uses_pipeline_contract_and_counts_natural_nap():
    from tools.paired_eval import _served_verdict, abstention_summary

    # Explicit contract fields, no warning strings at all.
    assert _served_verdict({
        "class_name": "leaf_spot", "abstained": True, "abstention_reason": "ood",
    }) == (ABSTAIN_SENTINEL, "ood")
    assert _served_verdict({
        "class_name": "unknown", "warnings": ["blurry_image"],
    }) == (ABSTAIN_SENTINEL, "image_quality")
    # A natural NAP argmax stays a prediction of that class (correct on a
    # not_a_plant row, wrong on a plant row) but is coverage loss.
    assert _served_verdict({
        "class_name": "not_a_plant", "abstained": True,
        "abstention_reason": "not_a_plant",
    }) == ("not_a_plant", "not_a_plant")
    assert _served_verdict({
        "class_name": "healthy", "warnings": ["low_confidence"],
    }) == ("healthy", None)

    summary = abstention_summary(
        [ABSTAIN_SENTINEL, "not_a_plant", "healthy", "leaf_spot"],
        ["ood", "not_a_plant", None, None])
    assert summary["count"] == 1 and summary["rate"] == 0.25
    assert summary["natural_not_a_plant"] == 1
    assert summary["coverage"] == 0.5
    assert summary["by_reason"] == {"not_a_plant": 1, "ood": 1}


def _rgb_images(root: Path, n: int) -> list[Path]:
    from PIL import Image

    paths = []
    for i in range(n):
        path = root / f"img_{i}.png"
        Image.new("RGB", (8, 8), (i * 20, 100, 50)).save(path)
        paths.append(path)
    return paths


def test_predict_scores_the_bound_graph_in_batches(tmp_path, monkeypatch):
    """``predict`` loads the manifest-selected graph exactly and keeps row order
    across batch boundaries."""
    import classifier

    write_bundle(tmp_path / "bundle")
    paths = _rgb_images(tmp_path, 5)
    seen: dict = {"batches": []}

    class FakeClassifier:
        CLASS_NAMES = ["healthy", "leaf_spot"]
        img_size, eval_resize_ratio, tta, architecture = 224, 1.05, "none", "fake"

        def __init__(self, model_path, *, exact_model_path):
            seen["model_path"], seen["exact"] = Path(model_path), exact_model_path

        def classify_batch(self, images):
            seen["batches"].append(len(images))
            return [
                {"class_name": "leaf_spot" if im.getpixel((0, 0))[0] >= 40 else "healthy"}
                for im in images
            ]

    monkeypatch.setattr(classifier, "DiseaseClassifier", FakeClassifier)
    from tools.paired_eval import predict

    preds = predict(tmp_path / "bundle", paths, batch=2, label="A")
    assert preds == ["healthy", "healthy", "leaf_spot", "leaf_spot", "leaf_spot"]
    assert seen["batches"] == [2, 2, 1]
    assert seen["exact"] is True
    assert seen["model_path"] == (tmp_path / "bundle" / "classifier_int8.onnx").resolve()


def test_predict_served_runs_the_pipeline_and_reports_abstentions(tmp_path, monkeypatch):
    """``predict_served`` scores what the API returns: gate rejections become the
    abstention sentinel with their reason, a natural not_a_plant stays a
    prediction, and the pipeline is told to require the bundle manifest."""
    import pipeline

    write_bundle(tmp_path / "bundle")
    paths = _rgb_images(tmp_path, 3)
    responses = iter([
        {"class_name": "healthy"},
        {"class_name": "leaf_spot", "abstained": True, "abstention_reason": "ood"},
        {"class_name": "not_a_plant", "abstained": True,
         "abstention_reason": "not_a_plant"},
    ])
    seen: dict = {}

    class FakePipeline:
        def __init__(self, detector_path, classifier_path, *, require_bundle_manifest):
            seen["detector"] = Path(detector_path)
            seen["classifier"] = Path(classifier_path)
            seen["require_manifest"] = require_bundle_manifest

        def analyze(self, image):
            assert image.mode == "RGB"
            return next(responses)

    monkeypatch.setattr(pipeline, "PlantDiseasePipeline", FakePipeline)
    from tools.paired_eval import predict_served

    reasons: list = []
    preds = predict_served(tmp_path / "bundle", paths, label="B", reasons=reasons)
    assert preds == ["healthy", ABSTAIN_SENTINEL, "not_a_plant"]
    assert reasons == [None, "ood", "not_a_plant"]
    assert seen["require_manifest"] is True
    assert seen["detector"] == tmp_path / "bundle" / "detector.onnx"
    assert seen["classifier"].name == "classifier_int8.onnx"
