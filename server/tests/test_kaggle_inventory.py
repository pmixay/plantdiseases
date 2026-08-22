"""Tests for the uncapped Kaggle lister and its benchmark gate.

Both behaviours under test are ones the tool got wrong in production:

* ``listing`` must RAISE when it runs out of pages with a live page token. The
  2026-08-15 survey concluded "public abiotic-scorch imagery is exhausted" from
  a lister that capped at 40 pages and returned silently; re-listing one dataset
  uncapped went 24,000 -> 49,878 files.
* ``gate`` must estimate augmentation collapse from a CONTIGUOUS sample. Reading
  it off the evenly-strided contamination sample is how ``Hibiscus_Necrosis`` was
  recorded as ~500 distinct photographs when the folder holds 37.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from tools import kaggle_inventory as ki

REF = "owner/slug"


def _write(path, seed, gain=1.0):
    """A low-frequency image (so pHash is stable) with optional brightness jitter.

    Brightness/quality jitter is the augmentation pHash *does* catch, and it is
    what the real collapsed folder used. Rotation is a different problem —
    pHash does not see rotated copies as duplicates at all, which is why the
    tool's docstring says to count by original stem where a source exposes one.
    """
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    im = Image.fromarray(small).resize((192, 192), Image.BICUBIC)
    if gain != 1.0:
        arr = np.clip(np.asarray(im, dtype=np.float32) * gain, 0, 255).astype(np.uint8)
        im = Image.fromarray(arr)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=95)


def test_listing_raises_rather_than_truncating(monkeypatch):
    """A silent truncation is indistinguishable from a small dataset."""
    monkeypatch.setattr(ki, "_api", lambda url, tries=5: {
        "datasetFiles": [{"name": "a.jpg"}], "nextPageToken": "always-more"})
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        ki.listing(REF, hard_cap=3)


def test_listing_returns_when_pages_run_out(monkeypatch):
    pages = [{"datasetFiles": [{"name": "a.jpg"}], "nextPageToken": "t"},
             {"datasetFiles": [{"name": "b.jpg"}], "nextPageToken": None}]
    monkeypatch.setattr(ki, "_api", lambda url, tries=5: pages.pop(0))
    assert ki.listing(REF) == ["a.jpg", "b.jpg"]


@pytest.fixture
def augmented_folder(tmp_path, monkeypatch):
    """40 photographs x 10 brightness-jittered copies, copies sorted adjacently.

    This is the collapsed-export shape: the folder looks like 400 images and
    holds 40. An evenly-strided sample of 40 steps over every duplicate.
    """
    src = tmp_path / "src"
    names = []
    for orig in range(40):
        for copy in range(10):
            n = f"Hibiscus_Necrosis/{orig:03d}_{copy}.jpg"
            _write(src / n, seed=orig, gain=1.0 + 0.02 * copy)
            names.append(n)

    monkeypatch.setattr(ki, "listing", lambda ref, hard_cap=2000: list(names))
    monkeypatch.setattr(ki, "download_file",
                        lambda ref, name, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                                 dest.write_bytes((src / name).read_bytes()),
                                                 True)[-1])
    monkeypatch.setattr(ki.time, "sleep", lambda *_: None)

    bench = tmp_path / "bench"
    _write(bench / "b.jpg", seed=9999)
    return bench, tmp_path / "work"


def test_augmentation_collapse_is_measured_not_stepped_over(augmented_folder):
    bench, work = augmented_folder
    r = ki.gate(REF, "Hibiscus_Necrosis", bench, sample=40, workdir=work)

    assert r["files"] == 400
    # The strided sample lands on 40 different originals and sees no collapse —
    # this is precisely the misleading number, so it must NOT be the ratio.
    assert r["distinct_in_spread_sample"] == 40
    # The contiguous block does see it.
    assert r["augmentation_ratio"] > 3.0, r
    assert r["est_distinct_in_folder"] < 120, r


def test_clean_folder_is_not_reported_as_contaminated(augmented_folder):
    bench, work = augmented_folder
    r = ki.gate(REF, "Hibiscus_Necrosis", bench, sample=20, workdir=work)
    assert r["benchmark_hits_at_hamming6"] == 0
    assert r["verdict"] == "clean of the benchmark"


def test_empty_folder_is_explicit(augmented_folder):
    bench, work = augmented_folder
    assert ki.gate(REF, "no_such_folder", bench, sample=10, workdir=work)["verdict"] == "EMPTY"


def test_strided_sample_reaches_the_end_of_the_folder(tmp_path, monkeypatch):
    """A contaminated TAIL must not report clean.

    `names[::len//sample]` looks like it spreads across a folder and does not:
    integer floor makes step==1 for any folder with sample <= len < 2*sample, so
    it degenerates into "the first `sample` files". A 119-file folder whose last
    59 files ARE the benchmark then reports "clean of the benchmark" — the exact
    failure this module exists to prevent.
    """
    src, bench = tmp_path / "src", tmp_path / "bench"
    _write(bench / "b.jpg", seed=7)
    names = []
    for i in range(119):
        n = f"F/{i:03d}.jpg"
        if i >= 60:                       # the tail IS the benchmark
            (src / n).parent.mkdir(parents=True, exist_ok=True)
            (src / n).write_bytes((bench / "b.jpg").read_bytes())
        else:
            _write(src / n, seed=1000 + i)
        names.append(n)

    monkeypatch.setattr(ki, "listing", lambda ref, hard_cap=2000: list(names))
    monkeypatch.setattr(ki, "download_file",
                        lambda ref, name, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                                 dest.write_bytes((src / name).read_bytes()),
                                                 True)[-1])
    monkeypatch.setattr(ki.time, "sleep", lambda *_: None)

    r = ki.gate(REF, "F", bench, sample=60, workdir=tmp_path / "w")
    assert r["benchmark_hits_at_hamming6"] > 0, f"contaminated tail went unsampled: {r}"
