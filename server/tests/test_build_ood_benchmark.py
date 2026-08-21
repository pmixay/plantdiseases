"""Tests for the OOD-benchmark builder (``tools/build_ood_benchmark.py``).

This tool had no coverage until 2026-08-18, and it is the one that decides what
the release-blocking gate grades against — a silent mistake here is invisible
until a model is judged by it.

The rule under test is the retirement of `banana_bush`. Its "scorch" folder is
turgor loss (wilting, foliage still green), not the marginal necrosis our
`leaf_scorch_underwatering` class means, and pooling the two produced the 0.565
headline three releases were judged against when the honest hibiscus-only figure
for the same bundle is 1.000. Retired arms must never reach disk, because the
ship gate joins images to the manifest by filename and an orphan image has no
species to attribute.
"""

from __future__ import annotations

import csv

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
    b.build(src, out, per_class_cap=100)
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
    b.build(src, out, per_class_cap=100)
    healthy = {r["species"] for r in _manifest(out) if r["class"] == "healthy"}
    assert "banana_bush" in healthy


def test_manifest_and_disk_agree_exactly(src, tmp_path):
    """The ship gate joins images to the manifest by filename; any drift on
    either side silently attributes an image to the wrong arm, or to none."""
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100)
    listed = {r["filename"] for r in _manifest(out)}
    on_disk = {f"{p.parent.name}/{p.name}" for p in out.rglob("*.jpg")}
    assert listed == on_disk


def test_arm_is_blank_for_healthy_and_named_for_disease(src, tmp_path):
    out = tmp_path / "bench"
    b.build(src, out, per_class_cap=100)
    for r in _manifest(out):
        if r["class"] == "healthy":
            assert r["arm"] == "", "a healthy leaf has no condition arm"
        else:
            assert r["arm"] == "necrosis"


def test_unknown_species_is_flagged_not_guessed(src, tmp_path, capsys):
    """An unlisted species must warn, never silently inherit an arm."""
    for i in range(6):
        _img(src / "train" / "mystery_plant" / "scorch" / f"{i}.jpg", seed=900 + i)
    b.build(src, tmp_path / "bench", per_class_cap=100)
    out = capsys.readouterr().out
    assert "mystery_plant" in out and "SPECIES_ARMS" in out
