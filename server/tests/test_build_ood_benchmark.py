"""Tests for the OOD-benchmark builder (``tools/build_ood_benchmark.py``).

This tool had no coverage until 2026-08-18.  The v2 artifact is now explicitly
diagnostic: rotated/noised pseudo-replicates make its nominal scorch count
unfit for a release gate.  A silent builder mistake is still invisible until a
model is judged by it, so the integrity checks remain fail-closed.

The rule under test is the retirement of `banana_bush`. Its "scorch" folder is
turgor loss (wilting, foliage still green), not the marginal necrosis our
`leaf_scorch_underwatering` class means, and pooling the two produced the 0.565
headline three releases were judged against when the honest hibiscus-only figure
for the same bundle is 1.000. Retired arms must never reach disk, because the
ship gate joins images to the manifest by filename and an orphan image has no
species to attribute.
"""

from __future__ import annotations

import collections
import csv
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools import build_ood_benchmark as b


def _img(path, seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(small).resize((256, 256), Image.BICUBIC).save(path, quality=95)


@pytest.fixture
def src(tmp_path):
    """Upstream layout: <split>/<species>/<condition>/<images>."""
    root = tmp_path / "flower_data"
    n = 0
    for species, conds in (("hibiscus", ("healthy", "scorch")),
                           ("banana_bush", ("healthy", "scorch")),
                           ("rose", ("healthy",))):
        for cond in conds:
            for i in range(6):
                n += 1
                _img(root / "train" / species / cond / f"{i}.jpg", seed=n)
    return root


def _manifest(out):
    with (out / "manifest.csv").open() as fh:
        return list(csv.DictReader(fh))


def test_retired_arm_never_reaches_disk(src, tmp_path):
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    rows = _manifest(out)

    diseased = {(r["species"], r["class"]) for r in rows
                if r["class"] != "healthy"}
    assert ("banana_bush", "leaf_scorch_underwatering") not in diseased
    assert ("hibiscus", "leaf_scorch_underwatering") in diseased

    scorch_files = [p.name for p in (out / "leaf_scorch_underwatering").glob("*.jpg")]
    assert scorch_files, "the surviving arm should still produce images"
    assert not [f for f in scorch_files if f.startswith("banana_bush__")], \
        "retired arm left orphan images on disk"


def test_healthy_side_of_a_retired_species_is_kept(src, tmp_path):
    """Retirement is about the CONDITION, not the species — banana_bush is an
    in-domain ornamental and its healthy frames are valid negatives."""
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    healthy = {r["species"] for r in _manifest(out) if r["class"] == "healthy"}
    assert "banana_bush" in healthy


def test_manifest_and_disk_agree_exactly(src, tmp_path):
    """The ship gate joins images to the manifest by filename; any drift on
    either side silently attributes an image to the wrong arm, or to none."""
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    listed = [r["filename"] for r in _manifest(out)]
    on_disk = [f"{p.parent.name}/{p.name}" for p in out.rglob("*.jpg")]
    # COUNTS, not sets. build() pools every split dir before copying, so two
    # photographs sharing a stem across splits used to write to one destination:
    # two manifest rows, one file. A set comparison collapses that on both sides
    # and passes.
    dupes = [k for k, v in collections.Counter(listed).items() if v > 1]
    assert not dupes, f"duplicate manifest filenames — images overwrote each other: {dupes[:5]}"
    assert len(listed) == len(on_disk), f"{len(listed)} rows vs {len(on_disk)} files"
    assert set(listed) == set(on_disk)


def test_same_stem_in_two_splits_does_not_overwrite(tmp_path):
    """Upstream numbers files per split, so `1.jpg` exists in train AND test."""
    root = tmp_path / "src"
    n = 0
    for split in ("train", "test"):
        for cond in ("healthy", "scorch"):
            for i in range(6):
                n += 1
                _img(root / split / "hibiscus" / cond / f"{i}.jpg", seed=n)
    out = tmp_path / "bench"
    b.build(root, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    rows = _manifest(out)
    assert len(rows) == len(list(out.rglob("*.jpg"))), \
        "cross-split stem collision: manifest rows outnumber files on disk"


def test_arm_is_blank_for_healthy_and_named_for_disease(src, tmp_path):
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    for r in _manifest(out):
        if r["class"] == "healthy":
            assert r["arm"] == "", "a healthy leaf has no condition arm"
        else:
            assert r["arm"] == "necrosis"


def test_unknown_species_is_flagged_not_guessed(src, tmp_path, capsys):
    """An unlisted species must warn, never silently inherit an arm."""
    for i in range(6):
        _img(src / "train" / "mystery_plant" / "scorch" / f"{i}.jpg", seed=900 + i)
    b.build(src, tmp_path / "bench", per_class_cap=100,
            min_clusters_per_class=1, robust_dedup=False)
    out = capsys.readouterr().out
    assert "mystery_plant" in out and "SPECIES_ARMS" in out


def test_manifest_provenance_is_stable_not_local_tmp_path(src, tmp_path):
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    for row in _manifest(out):
        assert row["source"] == b.SOURCE_DATASET_SLUG
        assert row["source_slug"] == b.SOURCE_DATASET_SLUG
        assert row["source_version"] == b.SOURCE_DATASET_VERSION
        assert row["license_id"] == b.SOURCE_LICENSE_ID
        assert row["source_url"] == b.SOURCE_URL
        assert row["rights_status"] == b.SOURCE_RIGHTS_STATUS
        assert row["release_eligible"] == "false"
        assert row["source_relative_path"]
        assert len(row["capture_family_id"]) == 16
        assert int(row["source_family_size"]) >= 1
        assert str(tmp_path) not in row["source"]


def test_default_robust_dedup_merges_geometric_augmentation_family(
        src, tmp_path, monkeypatch):
    """pHash-unique rotations/noisy edits must count as one capture family."""
    def fake_inliers(left, right, _cache=None):
        same_source_folder = left.parent == right.parent
        return 40 if same_source_folder and {left.stem, right.stem} == {"0", "1"} else 0

    monkeypatch.setattr(b, "_sift_homography_inliers", fake_inliers)
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100, min_clusters_per_class=1)
    rows = _manifest(out)
    grouped = [r for r in rows if int(r["source_family_size"]) == 2]
    assert grouped, "geometrically matched pseudo-replicates were counted separately"
    assert not any(r["source_relative_path"].endswith("/1.jpg") for r in grouped)


def test_cross_class_geometric_duplicate_is_integrity_error(src, tmp_path, monkeypatch):
    def fake_inliers(left, right, _cache=None):
        if left.parent.name != right.parent.name and left.stem == right.stem == "0":
            return b.SIFT_MIN_HOMOGRAPHY_INLIERS
        return 0

    monkeypatch.setattr(b, "_sift_homography_inliers", fake_inliers)
    with pytest.raises(RuntimeError, match="cross-class duplicate-family"):
        b.build(src, tmp_path / "bench", per_class_cap=100,
                min_clusters_per_class=1)


def test_large_robust_cluster_refuses_infeasible_all_pairs(monkeypatch):
    class FarHash:
        def __sub__(self, _other):
            return 64

    paths = [Path(f"{i}.jpg") for i in range(4)]
    monkeypatch.setattr(b, "_phash", lambda _path: FarHash())
    monkeypatch.setattr(b, "MAX_EXHAUSTIVE_SIFT_IMAGES", 3)
    with pytest.raises(RuntimeError, match="family-manifest.*pHash-only fallback is unsafe"):
        b.cluster_paths(paths, robust=True)


def test_precomputed_family_map_scales_without_silent_phash_fallback(monkeypatch):
    class FarHash:
        def __sub__(self, _other):
            return 64

    paths = [Path(f"{i}.jpg") for i in range(4)]
    family_ids = {p: ("same" if p.name in {"0.jpg", "1.jpg"} else p.stem)
                  for p in paths}
    monkeypatch.setattr(b, "_phash", lambda _path: FarHash())
    monkeypatch.setattr(b, "MAX_EXHAUSTIVE_SIFT_IMAGES", 3)
    monkeypatch.setattr(
        b, "_sift_homography_inliers",
        lambda *_args, **_kwargs: pytest.fail("precomputed workflow ran all-pairs SIFT"))
    groups = b.cluster_paths(paths, robust=True, family_ids=family_ids)
    assert [p.name for p in groups[0]] == ["0.jpg", "1.jpg"]
    assert len(groups) == 3


def test_class_cap_is_a_real_cap_even_with_many_species(tmp_path):
    root = tmp_path / "src"
    for i in range(8):
        _img(root / "train" / f"species_{i}" / "healthy" / "x.jpg", seed=1000 + i)
    for i in range(3):
        _img(root / "train" / "hibiscus" / "scorch" / f"{i}.jpg", seed=1100 + i)
    out = tmp_path / "bench"
    b.build(root, out, per_class_cap=3, min_clusters_per_class=1,
            robust_dedup=False)
    counts = collections.Counter(r["class"] for r in _manifest(out))
    assert counts["healthy"] == 3
    assert all(n <= 3 for n in counts.values())


def test_thin_gate_counts_zero_and_raises_before_writing(src, tmp_path):
    out = tmp_path / "bench"
    with pytest.raises(RuntimeError, match="under 999 independent capture families"):
        b.build(src, out, per_class_cap=100, min_clusters_per_class=999,
                robust_dedup=False)
    assert not out.exists()


def test_refuses_nested_output_that_could_delete_source(src):
    with pytest.raises(ValueError, match="non-nested"):
        b.build(src, src / "bench", per_class_cap=10,
                min_clusters_per_class=1, robust_dedup=False)


def test_report_separates_abstention_from_false_fire(tmp_path, capsys):
    """A model that abstains on half its healthy images and one that calls
    half of them scorched used to print identical healthy rows."""
    import json

    names = ["healthy", "leaf_scorch_underwatering", "leaf_spot"]
    items = [f"healthy/h{i}.jpg" for i in range(10)]
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "species", "arm"])
        w.writeheader()
        w.writerows({"filename": it, "class": "healthy", "species": "rose", "arm": ""}
                    for it in items)
    preds = tmp_path / "preds.json"
    preds.write_text(json.dumps({
        "class_names": names, "items": items, "y_true": [0] * 10,
        "y_pred_a": [-1] * 5 + [0] * 5,   # abstains on half, never false-fires
        "y_pred_b": [1] * 5 + [0] * 5,    # calls half of them scorched
        "label_a": "abstainer", "label_b": "alarmist"}))
    b.report(preds, manifest)
    lines = capsys.readouterr().out.splitlines()
    recall = next(ln.split() for ln in lines if ln.split()[:2] == ["healthy", "0.500"])
    assert recall[1:] == ["0.500", "0.500", "+0.000", "10", "5", "0"]
    false_fire = next(ln.split() for ln in lines if ln.startswith("healthy FALSE-FIRE"))
    assert false_fire[-4:] == ["0.000", "0.500", "+0.500", "10"]


def test_undecodable_image_is_an_error_not_a_silent_drop(tmp_path):
    good = tmp_path / "ok.jpg"
    _img(good, 1)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(RuntimeError, match="could not be decoded"):
        b.cluster_paths([good, bad], robust=False)
