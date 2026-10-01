"""Tests for the supplement QA filter (``tools/filter_supplement.py``).

Every gate here has already cost this project something, so each one gets a
test that fails if the gate stops firing:

* ``bench_leak`` — the ``aditisalvi04`` incident, where 1,200 files that looked
  like the missing supply were the OOD benchmark's own photographs. A leak is
  a non-zero exit, not a log line.
* ``dup_cross`` — one photograph under two class labels. Both copies are
  withheld, because the tool has no way to know which label is the wrong one.
* ``dup_intra`` — the reason "663 leaf_scorch rows" and "663 leaf_scorch
  photographs" are different claims.
* ``blurry`` — a frame the serving blur gate would short-circuit to
  ``not_a_plant`` is not a usable disease example, but *is* a usable
  ``not_a_plant`` one. The asymmetry is deliberate and pinned below.

The fixtures are synthesised rather than sampled so the expected verdict of
every row is known by construction.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools import filter_supplement as fs

TOOL = Path(fs.__file__)


def _noise(path: Path, seed: int, size: tuple[int, int] = (256, 256)) -> None:
    """A sharp, pHash-distinct image: white noise survives both gates."""
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, quality=95)


def _gradient(path: Path, size: tuple[int, int] = (256, 256)) -> None:
    """A smooth ramp: variance-of-Laplacian ~0, so the blur gate must fire."""
    x = np.linspace(0, 255, size[0], dtype=np.float32)
    arr = np.repeat(x[None, :], size[1], axis=0).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([arr] * 3)).save(path, quality=95)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def _phash_of(path: Path) -> str:
    import imagehash

    with Image.open(path) as im:
        return str(imagehash.phash(im.convert("RGB")))


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A supplement + benchmark whose every row has a known correct verdict."""
    root, bench = tmp_path / "supp", tmp_path / "bench"

    # The benchmark: one photograph we must never find inside the corpus.
    _noise(bench / "leaf_scorch_underwatering" / "b0.jpg", seed=900)
    with (bench / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "capture_family_id"])
        w.writeheader()
        w.writerow({"filename": "leaf_scorch_underwatering/b0.jpg",
                    "class": "leaf_scorch_underwatering",
                    "capture_family_id": "benchmark-family-0"})

    expected = {}

    def row(name: str, verdict: str) -> str:
        expected[name] = verdict
        return name

    _noise(root / (a := row("leaf_spot/a.jpg", "keep")), seed=1)
    _noise(root / (b := row("leaf_spot/b.jpg", "keep")), seed=2)

    # Byte-identical copy inside one class → the second one is redundancy.
    dup = row("leaf_spot/a_copy.jpg", "dup_intra")
    _copy(root / a, root / dup)

    # The same photograph filed under a second class → label contradiction,
    # so BOTH copies are withheld (note b flips from keep to dup_cross).
    cross = row("healthy/b_copy.jpg", "dup_cross")
    _copy(root / b, root / cross)
    expected[b] = "dup_cross"

    # The benchmark, sitting in the corpus. This is the stop-the-line row.
    leak = row("leaf_scorch_underwatering/leak.jpg", "bench_leak")
    _copy(bench / "leaf_scorch_underwatering" / "b0.jpg", root / leak)

    # Blur is disqualifying for a disease class...
    _gradient(root / row("leaf_spot/smooth.jpg", "blurry"))
    # ...and unremarkable for not_a_plant, which is where the blur gate routes.
    _gradient(root / row("not_a_plant/smooth.jpg", "keep"))

    _noise(root / row("healthy/small.jpg", "tiny"), seed=3, size=(32, 32))

    corrupt = row("healthy/corrupt.jpg", "corrupt")
    (root / corrupt).parent.mkdir(parents=True, exist_ok=True)
    (root / corrupt).write_bytes(b"not a jpeg at all")

    gone = row("healthy/gone.jpg", "missing")

    # Present and decodable, but not the file the manifest describes.
    _noise(root / (swapped := row("healthy/swapped.jpg", "phash_mismatch")), seed=4)

    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["filename", "class", "split_hint", "phash",
                            "immutable_source_id", "capture_family_id",
                            "first_seen_model_version"])
        w.writeheader()
        for name in expected:
            src = root / name
            if name == swapped:
                digest = "0" * 16
            elif name == gone:
                digest = ""
            else:
                digest = _phash_of(src) if src.exists() and src.stat().st_size > 100 else ""
            w.writerow({"filename": name, "class": name.split("/")[0],
                        "split_hint": "eval_ok", "phash": digest,
                        "immutable_source_id": "supp:" + name,
                        "capture_family_id": (
                            "benchmark-family-0" if name.endswith("/leak.jpg")
                            else "supp-family:" + name),
                        "first_seen_model_version": "never"})

    return root, bench, expected


def _verdicts(rows: list[dict]) -> dict[str, str]:
    return {r["filename"]: r["filter_verdict"] for r in rows}


def test_every_gate_fires_on_its_own_row(bundle):
    root, bench, expected = bundle
    rows, report = fs.filter_bundle(root, bench, workers=4, require_provenance=True)
    assert _verdicts(rows) == expected
    assert report["totals"]["benchmark_leaks"] == 1


def test_blur_gate_is_asymmetric_for_not_a_plant(bundle):
    """Same smooth image, two classes, two verdicts — this is the point."""
    root, bench, _ = bundle
    got = _verdicts(fs.filter_bundle(root, bench, workers=4, require_provenance=True)[0])
    assert got["leaf_spot/smooth.jpg"] == "blurry"
    assert got["not_a_plant/smooth.jpg"] == "keep"


def _flip_bits(digest: str, n: int) -> str:
    """Perturb a 64-bit hex pHash by exactly ``n`` bits."""
    return f"{int(digest, 16) ^ ((1 << n) - 1):016x}"


def test_reencode_drift_is_not_a_mismatch(tmp_path):
    """A digest a few bits off is JPEG re-encoding, not a different photo.

    Exact equality condemned 131 of the v8 supplement's 3,389 rows; every one
    of them was 2 or 4 bits from its manifest digest, i.e. the same image.
    """
    root = tmp_path / "supp"
    _noise(root / "healthy/near.jpg", seed=7)
    _noise(root / "healthy/far.jpg", seed=8)
    near = _phash_of(root / "healthy/near.jpg")

    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "split_hint", "phash"])
        w.writeheader()
        w.writerow({"filename": "healthy/near.jpg", "class": "healthy",
                    "split_hint": "eval_ok", "phash": _flip_bits(near, 4)})
        w.writerow({"filename": "healthy/far.jpg", "class": "healthy",
                    "split_hint": "eval_ok",
                    "phash": _flip_bits(_phash_of(root / "healthy/far.jpg"), 20)})

    got = _verdicts(fs.filter_bundle(
        root, None, workers=2, require_provenance=False)[0])
    assert got["healthy/near.jpg"] == "keep", "4 bits of re-encode drift is the same image"
    assert got["healthy/far.jpg"] == "phash_mismatch", "20 bits is a different image"


def test_unreadable_digest_is_a_manifest_defect_not_a_row_defect(tmp_path):
    """A junk digest impugns the manifest, so it is counted, not condemned."""
    root = tmp_path / "supp"
    _noise(root / "healthy/h.jpg", seed=9)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "split_hint", "phash"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "healthy",
                    "split_hint": "eval_ok", "phash": "zzz"})

    rows, report = fs.filter_bundle(
        root, None, workers=2, require_provenance=False)
    assert _verdicts(rows) == {"healthy/h.jpg": "keep"}
    assert report["totals"]["unreadable_manifest_digests"] == 1


def test_blur_threshold_is_the_serving_one():
    """If the pipeline moves its floor, this tool moves with it — no copy."""
    assert fs.BLUR_FLOOR == fs._pipe.BLUR_LAPLACIAN_FLOOR


def test_counts_are_clusters_not_files(bundle):
    """A class of near-duplicates must not report its file count as coverage."""
    root, bench, _ = bundle
    _, report = fs.filter_bundle(root, bench, workers=4, require_provenance=True)
    spot = report["per_class"]["leaf_spot"]
    assert spot["manifest_rows"] > spot["distinct_clusters"]
    assert spot["inflation"] > 1.0


def test_clean_bundle_keeps_everything(tmp_path):
    root = tmp_path / "supp"
    for i in range(5):
        _noise(root / f"healthy/h{i}.jpg", seed=100 + i)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "split_hint", "phash"])
        w.writeheader()
        for i in range(5):
            name = f"healthy/h{i}.jpg"
            w.writerow({"filename": name, "class": "healthy", "split_hint": "eval_ok",
                        "phash": _phash_of(root / name)})

    rows, report = fs.filter_bundle(
        root, None, workers=4, require_provenance=False)
    assert set(_verdicts(rows).values()) == {"keep"}
    assert report["per_class"]["healthy"]["distinct_clusters"] == 5
    assert report["totals"]["benchmark_leaks"] == 0


def test_leak_is_a_non_zero_exit(bundle):
    """A contaminated corpus must break a pipeline, not just annotate one."""
    root, bench, _ = bundle
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), "--bench", str(bench),
         "--out", str(root.parent / "rep"), "--workers", "4"],
        capture_output=True, text=True, check=False)
    assert proc.returncode != 0
    assert "STOP" in proc.stdout + proc.stderr
    assert (root.parent / "rep.json").exists(), "the report is written before the exit"


@pytest.mark.parametrize("junk", ["0x123456789abcde", "1_23456789abcdef",
                                  "-123456789abcdef", "zzzzzzzzzzzzzzzz"])
def test_malformed_digest_never_crashes_the_run(tmp_path, junk):
    """A junk manifest digest is a MANIFEST defect and must be absorbed.

    The guard used to be int(recorded, 16), which accepts '0x' prefixes, '_'
    separators and a leading '-' — none of which _to_bits can parse. Those
    digests passed the guard and then took the whole run down, after the full
    corpus decode, on exactly the input the guard exists to catch.
    """
    root = tmp_path / "supp"
    _noise(root / "healthy/h.jpg", seed=11)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "split_hint", "phash"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "healthy",
                    "split_hint": "eval_ok", "phash": junk})

    rows, report = fs.filter_bundle(
        root, None, workers=2, require_provenance=False)
    assert _verdicts(rows) == {"healthy/h.jpg": "keep"}
    assert report["totals"]["unreadable_manifest_digests"] == 1


def test_scan_keys_are_posix_relative_on_every_platform(tmp_path):
    _noise(tmp_path / "healthy" / "h.jpg", seed=20)
    assert set(fs._scan(tmp_path, workers=1)) == {"healthy/h.jpg"}


def test_manifest_duplicate_filename_is_rejected(tmp_path):
    root = tmp_path / "supp"
    _noise(root / "healthy/h.jpg", seed=21)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "healthy"})
        w.writerow({"filename": "healthy\\h.jpg", "class": "healthy"})
    with pytest.raises(SystemExit, match="duplicate filename"):
        fs.filter_bundle(root, None, workers=1, require_provenance=False)


def test_manifest_class_must_match_path(tmp_path):
    root = tmp_path / "supp"
    _noise(root / "healthy/h.jpg", seed=22)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "leaf_spot"})
    with pytest.raises(SystemExit, match="disagrees"):
        fs.filter_bundle(root, None, workers=1, require_provenance=False)


def test_empty_benchmark_refuses_to_certify_clean(tmp_path):
    root, bench = tmp_path / "supp", tmp_path / "empty_bench"
    _noise(root / "healthy/h.jpg", seed=23)
    bench.mkdir()
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "phash"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "healthy",
                    "phash": _phash_of(root / "healthy/h.jpg")})
    with pytest.raises(RuntimeError, match="no decodable images"):
        fs.filter_bundle(root, bench, workers=1, require_provenance=False)


def test_benchmark_leak_overrides_blur_quality_verdict(tmp_path):
    root, bench = tmp_path / "supp", tmp_path / "bench"
    _gradient(root / "leaf_spot/leak.jpg")
    _copy(root / "leaf_spot/leak.jpg", bench / "leaf_spot/leak.jpg")
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "phash"])
        w.writeheader()
        w.writerow({"filename": "leaf_spot/leak.jpg", "class": "leaf_spot",
                    "phash": _phash_of(root / "leaf_spot/leak.jpg")})
    rows, report = fs.filter_bundle(
        root, bench, workers=1, require_provenance=False)
    assert _verdicts(rows)["leaf_spot/leak.jpg"] == "bench_leak"
    assert report["totals"]["benchmark_leaks"] == 1


def test_strict_filter_requires_stable_capture_provenance_only_when_asked(tmp_path):
    """A v8-shaped manifest (no provenance columns) is accepted by default and
    labelled as pHash-only; the v9 contract is an explicit opt-in."""
    root = tmp_path / "supp"
    _noise(root / "healthy/h.jpg", seed=30)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "phash"])
        w.writeheader()
        w.writerow({"filename": "healthy/h.jpg", "class": "healthy",
                    "phash": _phash_of(root / "healthy/h.jpg")})
    rows, report = fs.filter_bundle(root, None, workers=1)
    assert _verdicts(rows)["healthy/h.jpg"] == "keep"
    assert report["thresholds"]["independence_method"] == "legacy_phash_only_NOT_CERTIFIED"
    assert {row["filter_independence_method"] for row in rows} == {
        "legacy_phash_only_NOT_CERTIFIED"}
    with pytest.raises(SystemExit, match="capture_family_id"):
        fs.filter_bundle(root, None, workers=1, require_provenance=True)


def test_capture_family_merges_phash_distinct_siblings(tmp_path):
    root = tmp_path / "supp"
    _noise(root / "healthy/a.jpg", seed=31)
    _noise(root / "healthy/b.jpg", seed=32)
    fields = ["filename", "class", "phash", "immutable_source_id",
              "capture_family_id", "first_seen_model_version"]
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for name in ("healthy/a.jpg", "healthy/b.jpg"):
            w.writerow({"filename": name, "class": "healthy",
                        "phash": _phash_of(root / name),
                        "immutable_source_id": "source:" + name,
                        "capture_family_id": "one-underlying-capture",
                        "first_seen_model_version": "never"})
    rows, report = fs.filter_bundle(root, None, workers=1, require_provenance=True)
    assert sorted(_verdicts(rows).values()) == ["dup_intra", "keep"]
    assert report["thresholds"]["independence_method"].startswith(
        "curator_capture_family_id")
    assert {row["filter_independence_method"] for row in rows} == {
        "capture_family_id+phash_v1"}


def _plain_manifest(root: Path, names: list[str], cols=("filename", "class", "split_hint", "phash")):
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cols))
        w.writeheader()
        for name in names:
            row = {"filename": name, "class": name.split("/")[0], "split_hint": "eval_ok",
                   "phash": _phash_of(root / name)}
            w.writerow({c: row.get(c, "") for c in cols})


def test_unlisted_photos_are_adopted_train_only_and_qad(tmp_path):
    """Light collection: dropping photos into a class folder is a submission.
    They must still pass every gate — a duplicate of a listed photo is caught —
    and must never become eval rows by being adopted."""
    root = tmp_path / "supp"
    _noise(root / "healthy/h0.jpg", seed=200)
    _noise(root / "healthy/h1.jpg", seed=201)
    _plain_manifest(root, ["healthy/h0.jpg", "healthy/h1.jpg"])
    _noise(root / "leaf_spot/new_scan.jpg", seed=202)              # new photo
    _copy(root / "healthy/h0.jpg", root / "healthy/h0_again.jpg")  # unlisted duplicate
    (root / "leaf_spot/new_scan.txt").write_text("0 0.5 0.5 0.3 0.3\n")
    _noise(root / "loose_at_root.jpg", seed=203)                   # no class folder

    rows, report = fs.filter_bundle(root, None, workers=2)
    by = {r["filename"]: r for r in rows}
    assert by["leaf_spot/new_scan.jpg"]["split_hint"] == "train_only"
    assert by["leaf_spot/new_scan.jpg"]["filter_verdict"] == "keep"
    assert by["leaf_spot/new_scan.jpg"]["phash"] == _phash_of(root / "leaf_spot/new_scan.jpg")
    assert by["healthy/h0_again.jpg"]["filter_verdict"] == "dup_intra"
    assert "loose_at_root.jpg" not in by
    t = report["totals"]
    assert t["adopted_unlisted"] == 2 and t["orphan_files"] == 1
    assert t["kept_rows_with_box_file"] == 1
    assert {tuple(r) for r in rows} == {tuple(rows[0])}, "every row needs the same columns"


def test_adoption_never_creates_a_routing_basis(tmp_path):
    """Without split_hint/confidence the notebook treats every listed row as
    eval-eligible, so adopting a photo there would PROMOTE it — not done."""
    root = tmp_path / "supp"
    _noise(root / "healthy/h0.jpg", seed=300)
    _plain_manifest(root, ["healthy/h0.jpg"], cols=("filename", "class", "phash"))
    _noise(root / "healthy/new.jpg", seed=301)
    rows, report = fs.filter_bundle(root, None, workers=1)
    assert [r["filename"] for r in rows] == ["healthy/h0.jpg"]
    assert report["totals"]["orphan_files"] == 1


def test_confidence_basis_adopts_with_a_blank_confidence(tmp_path):
    root = tmp_path / "supp"
    _noise(root / "healthy/h0.jpg", seed=400)
    _plain_manifest(root, ["healthy/h0.jpg"], cols=("filename", "class", "confidence"))
    _noise(root / "healthy/new.jpg", seed=401)
    rows, _ = fs.filter_bundle(root, None, workers=1)
    new = next(r for r in rows if r["filename"] == "healthy/new.jpg")
    assert new["confidence"] == "" and "split_hint" not in new


def test_opt_out_does_not_adopt(tmp_path):
    root = tmp_path / "supp"
    _noise(root / "healthy/h0.jpg", seed=500)
    _plain_manifest(root, ["healthy/h0.jpg"])
    _noise(root / "healthy/new.jpg", seed=501)
    rows, report = fs.filter_bundle(root, None, workers=1, adopt_unlisted=False)
    assert len(rows) == 1 and report["totals"]["orphan_files"] == 1
