"""Tests for the persistent frozen-eval pin (the cross-version paired-A/B fix).

These exercise the content-identity contract and the pin mechanics without a
model: sha256 is exact, the DCT pHash survives a JPEG re-encode but separates
different images, the manifest round-trips, and ``pin_frozen_eval`` moves
exactly the manifest images out of train into the held-out split, tolerates a
bounded number of missing entries, and fails fast when coverage collapses.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from tools import frozen_eval as fz


def _gradient(seed: int, size: int = 96) -> Image.Image:
    """A smooth image whose LOW-FREQUENCY structure is unique per seed.

    Built by upscaling an 8×8 random field, so different seeds differ exactly
    where the 8×8 DCT pHash looks (distinct hashes) while staying smooth enough
    to survive a JPEG re-encode (stable hash). Plain analytic gradients collapse
    to the same coarse signature and would make the test vacuous.
    """
    rng = np.random.default_rng(seed)
    low = rng.uniform(0, 1, size=(8, 8, 3))
    img = Image.fromarray((low * 255).astype(np.uint8), "RGB")
    return img.resize((size, size), Image.BICUBIC)


def _write(path, img: Image.Image, source="doctorp"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    path.with_suffix(".json").write_text(
        f'{{"source": "{source}", "plant_id": "{path.stem}"}}', encoding="utf-8"
    )


def test_sha256_exact_and_stable(tmp_path):
    p = tmp_path / "a.png"
    _gradient(1).save(p)
    s1 = fz.sha256_file(p)
    s2 = fz.sha256_file(p)
    assert s1 == s2 and len(s1) == 64
    # different bytes -> different sha
    p2 = tmp_path / "b.png"
    _gradient(2).save(p2)
    assert fz.sha256_file(p2) != s1


def test_phash_survives_reencode_but_separates_images(tmp_path):
    img = _gradient(7)
    png = tmp_path / "x.png"
    jpg = tmp_path / "x.jpg"
    img.save(png)
    img.save(jpg, quality=60)  # lossy re-encode
    h_png = fz.phash_file(png)
    h_jpg = fz.phash_file(jpg)
    # Same picture across a re-encode: within the default fuzzy threshold.
    assert fz.hamming(h_png, h_jpg) <= fz.DEFAULT_PHASH_THRESHOLD
    # A different picture: comfortably outside it.
    other = tmp_path / "y.png"
    _gradient(99).save(other)
    assert fz.hamming(h_png, fz.phash_file(other)) > fz.DEFAULT_PHASH_THRESHOLD


def test_manifest_roundtrip(tmp_path):
    entries = [
        fz.FrozenEntry("a" * 64, 0x0123456789abcdef, "leaf_spot", "inat", "a.jpg"),
        fz.FrozenEntry("b" * 64, 0xfedcba9876543210, "healthy", "doctorp", "b.jpg"),
    ]
    m = fz.FrozenManifest("val_realworld", 6, entries, {"manifest_version": "1"})
    out = tmp_path / "m.json"
    m.save(out)
    loaded = fz.FrozenManifest.load(out)
    assert not loaded.is_empty
    assert loaded.phash_threshold == 6
    assert {e.sha256 for e in loaded.entries} == {"a" * 64, "b" * 64}
    assert loaded.entries[0].phash == 0x0123456789abcdef


def _build_train(tmp_path):
    train = tmp_path / "train"
    imgs = {}
    for cls, seeds in {
        "leaf_scorch_underwatering": [10, 11, 12],
        "healthy": [20, 21],
    }.items():
        for s in seeds:
            p = train / cls / f"img_{s}.png"
            im = _gradient(s)
            _write(p, im)
            imgs[(cls, s)] = (p, im)
    return train, imgs


def test_pin_moves_exactly_manifest_images(tmp_path):
    train, imgs = _build_train(tmp_path)
    dest = tmp_path / "val_realworld"
    # Freeze two scorch images: one by exact sha, one by pHash (re-encoded copy).
    p10 = imgs[("leaf_scorch_underwatering", 10)][0]
    e_sha = fz.FrozenEntry(fz.sha256_file(p10), fz.phash_file(p10),
                           "leaf_scorch_underwatering", "doctorp", "img_10.png")
    im11 = imgs[("leaf_scorch_underwatering", 11)][1]
    e_ph = fz.FrozenEntry("0" * 64, fz.phash_image(im11),  # wrong sha -> phash path
                          "leaf_scorch_underwatering", "doctorp", "img_11.png")
    m = fz.FrozenManifest("val_realworld", 6, [e_sha, e_ph], {})
    stats = fz.pin_frozen_eval(train, dest, m, min_coverage=0.5, verbose=False)
    assert stats.matched_sha == 1
    assert stats.matched_phash == 1
    assert stats.missing == 0
    # Moved out of train, into dest, with sidecars.
    assert not (train / "leaf_scorch_underwatering" / "img_10.png").exists()
    assert (dest / "leaf_scorch_underwatering" / "img_10.png").exists()
    assert (dest / "leaf_scorch_underwatering" / "img_10.json").exists()
    assert (dest / "leaf_scorch_underwatering" / "img_11.png").exists()
    # Untouched image stays in train.
    assert (train / "leaf_scorch_underwatering" / "img_12.png").exists()


def test_pin_fails_fast_below_coverage(tmp_path):
    train, _ = _build_train(tmp_path)
    dest = tmp_path / "val_realworld"
    # A manifest dominated by images that don't exist in this build.
    ghosts = [
        fz.FrozenEntry(f"{i:064x}", i, "leaf_scorch_underwatering", "doctorp", f"g{i}.png")
        for i in range(9)
    ]
    real = train / "healthy" / "img_20.png"
    present = fz.FrozenEntry(fz.sha256_file(real), fz.phash_file(real),
                             "healthy", "doctorp", "img_20.png")
    m = fz.FrozenManifest("val_realworld", 6, ghosts + [present], {})
    with pytest.raises(RuntimeError, match="coverage"):
        fz.pin_frozen_eval(train, dest, m, min_coverage=0.90, verbose=False)


def test_pin_skips_cross_class_collision(tmp_path):
    train, imgs = _build_train(tmp_path)
    dest = tmp_path / "val_realworld"
    # Manifest says this sha is 'healthy' but in train it lives under scorch.
    p = imgs[("leaf_scorch_underwatering", 10)][0]
    e = fz.FrozenEntry(fz.sha256_file(p), fz.phash_file(p), "healthy", "x", "img_10.png")
    m = fz.FrozenManifest("val_realworld", 6, [e], {})
    stats = fz.pin_frozen_eval(train, dest, m, min_coverage=0.0, verbose=False)
    assert stats.cross_class_skipped == 1
    assert stats.matched == 0
    # The image is NOT relabelled/moved.
    assert (train / "leaf_scorch_underwatering" / "img_10.png").exists()


def test_empty_manifest_is_noop(tmp_path):
    train, _ = _build_train(tmp_path)
    dest = tmp_path / "val_realworld"
    m = fz.FrozenManifest("val_realworld", 6, [], {})
    stats = fz.pin_frozen_eval(train, dest, m, verbose=False)
    assert stats.matched == 0 and stats.moved_files == 0
    assert not dest.exists()
