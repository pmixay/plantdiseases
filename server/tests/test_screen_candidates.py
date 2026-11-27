"""tools/screen_candidates.py: the v9 pre-screen drops only objective defects.

The harness ships standalone in the collection task archive, so it carries
copies of the server's gates; these tests are what keeps the copies honest.
Its colour buckets only order the review queue, so the tests pin their
direction on drawn leaves, not exact values.
"""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

import pipeline
from tools import filter_supplement as fs
from tools import screen_candidates as sc

GREEN = (46, 125, 50)
BROWN = (120, 72, 30)


def _leaf(size=(640, 480), *, rim=0, spots=0, brown_body=False, seed=0) -> Image.Image:
    """A textured green ellipse on grey: a brown margin ``rim`` px wide, ``spots``
    interior lesions, or a mostly brown blade. Texture keeps it above the blur floor."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", size, (200, 200, 200))
    d = ImageDraw.Draw(img)
    # Seed-dependent size and position: differently seeded leaves must be
    # different photos to pHash in every rotation and mirror the gate checks.
    bw, bh = int(size[0] * rng.uniform(0.45, 0.8)), int(size[1] * rng.uniform(0.45, 0.8))
    x0, y0 = int(rng.integers(10, size[0] - bw - 10)), int(rng.integers(10, size[1] - bh - 10))
    box = (x0, y0, x0 + bw, y0 + bh)
    d.ellipse(box, fill=BROWN if brown_body else GREEN)
    if rim:
        d.ellipse(box, outline=BROWN, width=rim)
    for _ in range(spots):
        x = int(rng.integers(x0 + bw // 4, x0 + 3 * bw // 4))
        y = int(rng.integers(y0 + bh // 4, y0 + 3 * bh // 4))
        d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=BROWN)
    noise = rng.integers(-18, 18, size=(size[1], size[0], 3))
    return Image.fromarray(np.clip(np.asarray(img, dtype=np.int16) + noise, 0, 255).astype(np.uint8))


def _save(root, cls, name, img, boxes=None):
    path = root / cls / name
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=92)
    if boxes is not None:
        path.with_suffix(".txt").write_text(
            "".join(f"0 {cx} {cy} {w} {h}\n" for cx, cy, w, h in boxes), encoding="utf-8")
    return path


def test_shared_gates_match_the_server():
    assert sc.COLLECTION_MIN_SIDE == fs.COLLECTION_MIN_SIDE
    assert sc.BLUR_LAPLACIAN_FLOOR == pipeline.BLUR_LAPLACIAN_FLOOR
    assert sc.BLUR_DOWNSCALE == pipeline.BLUR_DOWNSCALE
    assert sc.PHASH_MAX_HAMMING == fs.PHASH_MAX_HAMMING


def test_head_classes_match_the_classifier():
    import classifier

    assert sc.HEAD_CLASSES == tuple(classifier.DEFAULT_CLASS_NAMES)


@pytest.mark.parametrize("seed", range(4))
def test_blur_metric_is_the_servers(seed):
    img = _leaf(seed=seed).filter(ImageFilter.GaussianBlur(seed))
    assert sc.variance_of_laplacian(img) == pytest.approx(pipeline._variance_of_laplacian(img), rel=1e-6)


def test_parse_yolo_reads_boxes_polygons_and_flags_bad_rows():
    boxes, ok = sc.parse_yolo("0 0.5 0.5 0.4 0.2\n0 0.1 0.1 0.3 0.1 0.3 0.4 0.1 0.4\n")
    assert ok and len(boxes) == 2
    assert boxes[1] == pytest.approx((0.2, 0.25, 0.2, 0.3))
    _, ok = sc.parse_yolo("0 0.9 0.9 0.5 0.5\n")
    assert not ok  # runs off the frame


def test_buckets_point_the_right_way():
    def bucket(cls, img):
        return sc.bucket_for(cls, sc.colour_measures(img, []))

    assert bucket("leaf_scorch_underwatering", _leaf(rim=6)) == "early_fast"
    assert bucket("leaf_scorch_underwatering", _leaf(brown_body=True, rim=4)) in {"late", "no_leaf_found"}
    assert bucket("leaf_scorch_underwatering", _leaf()) == "no_visible_damage"
    assert bucket("leaf_spot", _leaf(spots=8)) == "spots_fast"
    assert bucket("healthy", _leaf()) == "clean"
    assert bucket("healthy", _leaf(rim=6)) == "scorch_candidate"


def test_drops_only_objective_defects(tmp_path):
    root = tmp_path / "batch"
    _save(root, "leaf_scorch_underwatering", "a_early.jpg", _leaf(rim=6), [(0.5, 0.5, 0.8, 0.8)])
    _save(root, "leaf_scorch_underwatering", "b_copy.jpg", _leaf(rim=6), [(0.5, 0.5, 0.8, 0.8)])
    _save(root, "leaf_scorch_underwatering", "c_small.jpg", _leaf(size=(300, 240), rim=4, seed=1))
    _save(root, "leaf_spot", "d_blurry.jpg", _leaf(spots=8, seed=2).filter(ImageFilter.GaussianBlur(8)))
    _save(root, "healthy", "e_plain.jpg", _leaf(seed=3))
    _save(root, "leaf_spot", "f_spots.jpg", _leaf(spots=8, seed=4), [(0.5, 0.5, 0.8, 0.8)])
    (root / "leaf_spot" / "g_corrupt.jpg").write_bytes(b"not a jpeg")
    rows = {r["filename"]: r for r in sc.screen(root, workers=2)}
    assert rows["leaf_scorch_underwatering/a_early.jpg"]["verdict"] == "keep"
    assert rows["leaf_scorch_underwatering/b_copy.jpg"]["verdict"] == "drop:dup_in_batch"
    assert rows["leaf_scorch_underwatering/b_copy.jpg"]["dup_of"] == "leaf_scorch_underwatering/a_early.jpg"
    assert rows["leaf_scorch_underwatering/c_small.jpg"]["verdict"] == "drop:too_small"
    assert rows["leaf_spot/d_blurry.jpg"]["verdict"] == "drop:blurry"
    assert rows["leaf_spot/g_corrupt.jpg"]["verdict"] == "drop:corrupt"
    # Normal photos are never dropped, whatever their bucket.
    assert rows["healthy/e_plain.jpg"]["verdict"] == "keep"
    assert rows["leaf_spot/f_spots.jpg"]["verdict"] == "keep"
    # A stratum that needs boxes and has none is flagged, not dropped.
    assert "needs_boxes" in rows["leaf_scorch_underwatering/c_small.jpg"]["flags"]
    assert rows["leaf_spot/f_spots.jpg"]["flags"] == ""


def test_copy_under_another_class_is_a_label_contradiction(tmp_path):
    root = tmp_path / "batch"
    _save(root, "healthy", "same_photo.jpg", _leaf(rim=6, seed=5))
    _save(root, "leaf_scorch_underwatering", "same_photo.jpg", _leaf(rim=6, seed=5))
    rows = {r["filename"]: r for r in sc.screen(root, workers=1)}
    assert rows["leaf_scorch_underwatering/same_photo.jpg"]["verdict"] == "drop:dup_other_class"
    assert rows["healthy/same_photo.jpg"]["verdict"] == "keep"
    assert "dup_in_other_class" in rows["healthy/same_photo.jpg"]["flags"]


def test_rotated_copy_of_a_reference_is_dropped(tmp_path):
    ref = tmp_path / "v8"
    _save(ref, "leaf_scorch_underwatering", "ls_0001.jpg", _leaf(rim=6, seed=7))
    root = tmp_path / "batch"
    _save(root, "leaf_scorch_underwatering", "rotated.jpg", _leaf(rim=6, seed=7).rotate(90, expand=True))
    _save(root, "leaf_scorch_underwatering", "other.jpg", _leaf(spots=5, rim=3, seed=8))
    rows = {r["filename"]: r for r in sc.screen(root, against=[ref], workers=2)}
    assert rows["leaf_scorch_underwatering/rotated.jpg"]["verdict"] == "drop:dup_of_reference"
    assert rows["leaf_scorch_underwatering/other.jpg"]["verdict"] == "keep"


def _busy(seed: int) -> Image.Image:
    """A leaf scene with photo-like clutter: SIFT needs corners, drawn ellipses have few."""
    rng = np.random.default_rng(seed)
    img = _leaf(rim=6, spots=4, seed=seed)
    d = ImageDraw.Draw(img)
    for _ in range(400):
        x, y = int(rng.integers(0, 630)), int(rng.integers(0, 470))
        s = int(rng.integers(3, 12))
        colour = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.rectangle((x, y, x + s, y + s), fill=colour)
        else:
            d.line((x, y, x + s, y + s // 2), fill=colour, width=2)
    return img


def test_sift_drops_a_crop_and_keeps_an_unrelated_photo(tmp_path):
    ref = tmp_path / "v8"
    original = _busy(60)
    _save(ref, "leaf_scorch_underwatering", "ls_0002.jpg", original)
    root = tmp_path / "batch"
    # A crop survives pHash (it shifts the low frequencies) but not SIFT + RANSAC.
    _save(root, "leaf_scorch_underwatering", "crop.jpg", original.crop((60, 50, 600, 460)).resize((640, 486)))
    _save(root, "leaf_scorch_underwatering", "other.jpg", _busy(61))
    rows = {r["filename"]: r for r in sc.screen(root, against=[ref], sift=True, workers=1)}
    assert rows["leaf_scorch_underwatering/crop.jpg"]["verdict"] == "drop:dup_of_reference"
    assert "sift" in rows["leaf_scorch_underwatering/crop.jpg"]["dup_of"]
    assert rows["leaf_scorch_underwatering/other.jpg"]["verdict"] == "keep"
    assert "possible_copy_of_reference" not in rows["leaf_scorch_underwatering/other.jpg"]["flags"]


def test_benchmark_leak_and_synthetic_cap_stop_the_run(tmp_path):
    bench = tmp_path / "bench"
    _save(bench, "leaf_scorch_underwatering", "b1.jpg", _leaf(rim=6, seed=9))
    root = tmp_path / "batch"
    _save(root, "leaf_scorch_underwatering", "leak.jpg", _leaf(rim=6, seed=9))
    out = tmp_path / "report" / "r"
    assert sc.main(["--root", str(root), "--out", str(out), "--bench", str(bench), "--workers", "1"]) == 2
    assert json.loads(out.with_suffix(".json").read_text())["verdicts"] == {"drop:bench_leak": 1}

    root2 = tmp_path / "batch2"
    for i in range(3):
        _save(root2, "leaf_spot", f"real_{i}.jpg", _leaf(spots=6, seed=20 + i))
    _save(root2, "leaf_spot", "syn_0.jpg", _leaf(spots=6, seed=30))
    out2 = tmp_path / "report" / "r2"
    assert sc.main(["--root", str(root2), "--out", str(out2), "--workers", "1"]) == 3  # 1 of 4 > 5 %
    with out2.with_suffix(".csv").open(encoding="utf-8") as fh:
        rows = {r["filename"]: r for r in csv.DictReader(fh)}
    assert rows["leaf_spot/syn_0.jpg"]["split_hint"] == "train_only"  # synthetic never grades
    assert rows["leaf_spot/real_0.jpg"]["split_hint"] == "eval_ok"
    assert not out2.with_suffix(".csv").read_bytes().startswith(b"\xef\xbb\xbf")  # no BOM


def test_meta_adds_columns_and_filters_only_with_where(tmp_path):
    root = tmp_path / "supp"
    _save(root, "healthy", "hl_1.jpg", _leaf(rim=6, seed=40))
    _save(root, "healthy", "hl_2.jpg", _leaf(seed=41))
    meta = tmp_path / "manifest.csv"
    meta.write_text("﻿filename,hn_kind,capture_family_id\nhealthy/hl_1.jpg,boundary,fam_a\n",
                    encoding="utf-8")
    all_rows = sc.screen(root, meta=sc._read_meta(meta, []), workers=1)
    assert len(all_rows) == 2  # an unlisted photo is still screened
    only = sc.screen(root, meta=sc._read_meta(meta, ["hn_kind=boundary"]), meta_filters=True, workers=1)
    assert [r["filename"] for r in only] == ["healthy/hl_1.jpg"]
    assert only[0]["capture_family_id"] == "fam_a" and only[0]["meta_hn_kind"] == "boundary"
