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
import json
from pathlib import Path, PurePosixPath

import numpy as np
import pytest
from PIL import Image

from tools import build_ood_benchmark as b


def _img(path, seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(small).resize((256, 256), Image.BICUBIC).save(path, quality=95)


def _write_source(root):
    """Upstream layout: <split>/<species>/<condition>/<images>."""
    n = 0
    for species, conds in (("hibiscus", ("healthy", "scorch")),
                           ("banana_bush", ("healthy", "scorch")),
                           ("rose", ("healthy",))):
        for cond in conds:
            for i in range(6):
                n += 1
                _img(root / "train" / species / cond / f"{i}.jpg", seed=n)
    return root


@pytest.fixture
def src(tmp_path):
    return _write_source(tmp_path / "flower_data")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The default source built once, for the tests that only read the result.

    Five tests each regenerated the 30 source JPEGs and ran this identical
    build to inspect a different facet of its output.
    """
    root = tmp_path_factory.mktemp("ood-bench")
    source = _write_source(root / "flower_data")
    out = root / "bench"
    b.build(source, out, per_class_cap=100, min_clusters_per_class=1,
            robust_dedup=False)
    return source, out


def _manifest(out):
    with (out / "manifest.csv").open() as fh:
        return list(csv.DictReader(fh))


def test_retired_arm_never_reaches_disk(built):
    src, out = built
    rows = _manifest(out)

    diseased = {(r["species"], r["class"]) for r in rows
                if r["class"] != "healthy"}
    assert ("banana_bush", "leaf_scorch_underwatering") not in diseased
    assert ("hibiscus", "leaf_scorch_underwatering") in diseased

    scorch_files = [p.name for p in (out / "leaf_scorch_underwatering").glob("*.jpg")]
    assert scorch_files, "the surviving arm should still produce images"
    # Names are <split>__<species>__<stem>, so a prefix check never matched.
    assert not [f for f in scorch_files if "__banana_bush__" in f], \
        "retired arm left orphan images on disk"


def test_healthy_side_of_a_retired_species_is_kept(built):
    """Retirement is about the CONDITION, not the species — banana_bush is an
    in-domain ornamental and its healthy frames are valid negatives."""
    src, out = built
    healthy = {r["species"] for r in _manifest(out) if r["class"] == "healthy"}
    assert "banana_bush" in healthy


def test_manifest_and_disk_agree_exactly(built):
    """The ship gate joins images to the manifest by filename; any drift on
    either side silently attributes an image to the wrong arm, or to none."""
    src, out = built
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


def test_arm_is_blank_for_healthy_and_named_for_disease(built):
    src, out = built
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


def test_manifest_provenance_is_stable_not_local_tmp_path(built):
    src, out = built
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
        assert str(src.parent) not in row["source"]


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


# ── v3: declared sources, the non-plant arm, the manifest contract ───

NOTEBOOK = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"


def _first_party(root, source_id="np01", **extra):
    spec = {
        "id": source_id, "root": str(root), "origin": "first_party",
        "acquisition_batch_id": source_id, "layout": "session/condition",
        "license_id": "project-owned",
        "source_url": f"plantvision://acquisition/{source_id}",
        "creator": "collector-a",
        "conditions": {
            "empty_pot": {"class": "not_a_plant"},
            "foliage_print": {"class": "not_a_plant"},
        },
    }
    spec.update(extra)
    return spec


def _write_non_plant(root, seed=2000):
    for session in ("s1", "s2"):
        for kind in ("empty_pot", "foliage_print", "notes"):
            for i in range(3):
                seed += 1
                _img(root / session / kind / f"{i}.jpg", seed)
    return root


def _write_config(tmp_path, sources, slug=""):
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps({"benchmark_slug": slug, "sources": sources}))
    return cfg


def _plant_source(root, seed=3000):
    """<session>/<species>/<condition>, three species, two sessions."""
    for session in ("s1", "s2"):
        for species in ("hibiscus", "ficus", "pothos"):
            for cond in ("healthy", "scorch", "variegated"):
                for i in range(2):
                    seed += 1
                    _img(root / session / species / cond / f"{i}.jpg", seed)
    return {
        "id": "sc01", "root": str(root), "origin": "first_party",
        "acquisition_batch_id": "sc01", "layout": "session/species/condition",
        "license_id": "project-owned",
        "conditions": {
            "healthy": {"class": "healthy"},
            "scorch": {"class": "leaf_scorch_underwatering"},
            "variegated": {"class": "healthy", "arm": "difficult_plant"},
        },
        "species_arms": {"hibiscus": "necrosis", "ficus": "necrosis"},
    }


@pytest.fixture(scope="module")
def v3(tmp_path_factory):
    """A two-source v3-shaped build: plants with sessions, and non-plants."""
    root = tmp_path_factory.mktemp("v3")
    cfg = _write_config(root, [
        _plant_source(root / "plants"),
        _first_party(_write_non_plant(root / "nonplant"), release_eligible=True,
                     rights_verified_by="reviewer-b",
                     rights_status="project_owned_capture; P5 record np01"),
    ])
    sources, _slug = b.load_config(cfg)
    out = root / "bench"
    b.build_benchmark(sources, out, per_class_cap=100, min_clusters_per_class=1,
                      robust_dedup=False)
    return out


def test_non_plant_arm_alone_builds_first(tmp_path):
    """The plan sequences the non-plant arm first: it needs no plants. The
    legacy tool required `healthy` and `scorch` rows in every build."""
    cfg = _write_config(tmp_path, [_first_party(_write_non_plant(tmp_path / "np"))])
    sources, _slug = b.load_config(cfg)
    out = tmp_path / "bench"
    b.build_benchmark(sources, out, per_class_cap=100, min_clusters_per_class=1,
                      robust_dedup=False)
    rows = _manifest(out)
    assert {r["class"] for r in rows} == {"not_a_plant"}
    assert {r["arm"] for r in rows} == {"non_plant"}
    assert {r["negative_kind"] for r in rows} == {"empty_pot", "foliage_print"}
    assert {r["session"] for r in rows} == {"s1", "s2"}
    assert len(rows) == 12, "the unmapped `notes` folder must not be guessed into a class"
    assert [p.name for p in out.iterdir() if p.is_dir()] == ["not_a_plant"]


def test_v3_manifest_carries_what_the_plan_and_the_readers_need(v3):
    rows = _manifest(v3)
    for column in ("capture_family_id", "family_method", "source_family_size", "species",
                   "arm", "source_relative_path", "license_id", "source_url", "creator",
                   "rights_status", "release_eligible", "session", "negative_kind"):
        assert column in rows[0], column
    for r in rows:
        assert r["filename"].split("/")[0] == r["class"]
        assert (v3 / r["filename"]).is_file()
    families = [r["capture_family_id"] for r in rows]
    assert all(families) and len(set(families)) == len(families)
    by_arm = collections.Counter((r["class"], r["arm"]) for r in rows)
    assert by_arm == {
        ("healthy", ""): 12, ("healthy", "difficult_plant"): 12,
        ("leaf_scorch_underwatering", "necrosis"): 8,
        ("leaf_scorch_underwatering", "unassigned"): 4,     # pothos: not declared
        ("not_a_plant", "non_plant"): 12}
    # Rights are per source, and eligibility is never inherited across sources.
    eligible = {r["source_id"] for r in rows if r["release_eligible"] == "true"}
    assert eligible == {"np01"}
    assert {r["rights_verified_by"] for r in rows if r["source_id"] == "np01"} == {"reviewer-b"}
    assert {r["source_slug"] for r in rows} == {""}, "a first-party batch has no slug"
    assert {r["family_method"] for r in rows} == {b.UNCERTIFIED_FAMILY_METHOD}, \
        "a pHash-only build must not be labelled as SIFT-audited"
    assert {r["negative_kind"] for r in rows if r["arm"] == "difficult_plant"} == {"variegated"}
    assert {r["negative_kind"] for r in rows if r["arm"] in ("", "necrosis")} == {""}


def test_v3_build_info_records_sources_and_missed_targets(v3):
    info = json.loads((v3 / "build_info.json").read_text())
    assert [s["id"] for s in info["sources"]] == ["sc01", "np01"]
    assert info["families"]["not_a_plant[non_plant]"] == 12
    assert info["deny_list_check"] == "NOT RUN"
    notes = "\n".join(info["v3_target_notes"])
    assert "leaf_scorch_underwatering[necrosis]: 2 species, target 8-12" in notes
    assert "under 3 recorded sessions" in notes
    assert "under the ~100" in notes


def test_v3_targets_flag_one_species_dominating_an_arm():
    rows = [{"class": "leaf_scorch_underwatering", "arm": "necrosis",
             "species": sp, "session": f"s{i % 3}"}
            for sp, n in (("hibiscus", 9), ("ficus", 3), ("rose", 3)) for i in range(n)]
    notes = "\n".join(b.v3_target_notes(rows))
    assert "hibiscus holds 9/15 families" in notes
    assert "recorded sessions" not in notes


def test_v3_manifest_is_readable_by_the_family_joins(v3):
    """paired_eval --manifest and filter_supplement --bench both read this file."""
    from tools import filter_supplement, paired_eval

    rows = _manifest(v3)
    families = paired_eval._manifest_capture_families(v3 / "manifest.csv")
    assert len(set(families.values())) == len(rows)
    for r in rows:
        assert families[r["filename"].rsplit(".", 1)[0]] == r["capture_family_id"]
    assert filter_supplement._benchmark_family_ids(v3) == {
        r["capture_family_id"] for r in rows}
    paths, _y_true, stats = paired_eval.collect_eval_items(
        v3, b.load_class_names(), None, None, families)
    assert len(paths) == len(rows) and stats["dropped_unknown_class"] == 0
    assert all(stats["group_ids"])


def test_labels_come_from_the_classifier_taxonomy(tmp_path):
    import classifier

    assert b.load_class_names() == list(classifier.DEFAULT_CLASS_NAMES)
    assert set(b.CONDITION_MAP.values()) <= set(classifier.DEFAULT_CLASS_NAMES)
    assert {b.HEALTHY_CLASS, b.NAP_CLASS} <= set(classifier.DEFAULT_CLASS_NAMES)
    bundle = tmp_path / "classes.json"
    bundle.write_text(json.dumps({"class_names": ["healthy", "leaf_spot"]}))
    assert b.load_class_names(bundle) == ["healthy", "leaf_spot"]

    root = _write_non_plant(tmp_path / "np")
    spec = _first_party(root, conditions={"empty_pot": {"class": "non_plant"}})
    sources, _ = b.load_config(_write_config(tmp_path, [spec]))
    with pytest.raises(RuntimeError, match="not a classifier class"):
        b.build_benchmark(sources, tmp_path / "bench", 10, 1, robust_dedup=False)
    # A bundle without the rejection head cannot score a non-plant arm.
    sources, _ = b.load_config(_write_config(tmp_path, [_first_party(root)]))
    with pytest.raises(RuntimeError, match="not a classifier class"):
        b.build_benchmark(sources, tmp_path / "bench", 10, 1, robust_dedup=False,
                          class_names=["healthy", "leaf_spot"])
    assert not (tmp_path / "bench").exists()


@pytest.mark.parametrize("change, message", [
    ({"licence_id": "x"}, "unknown keys"),
    ({"origin": "internet"}, "'origin' must be one of"),
    ({"acquisition_batch_id": ""}, "needs 'acquisition_batch_id'"),
    ({"layout": "session/kind"}, "layout"),
    ({"layout": "session/condition", "session": "s9"}, "both a layout level and a constant"),
    ({"release_eligible": True}, "rights_verified_by"),
    ({"release_eligible": "true"}, "must be true or false"),
    ({"conditions": {"empty_pot": {"class": "healthy", "arm": "non_plant"}}}, "only"),
    ({"conditions": {"empty_pot": {"class": "not_a_plant", "arm": "difficult_plant"}}},
     "HEALTHY plant"),
    ({"conditions": {"leaf": {"class": "leaf_spot"}}}, "declares no species"),
    ({"origin": "kaggle"}, "needs 'slug'"),
])
def test_a_bad_source_declaration_is_refused(tmp_path, change, message):
    spec = _first_party(_write_non_plant(tmp_path / "np"), **change)
    with pytest.raises(RuntimeError, match=message):
        sources, _ = b.load_config(_write_config(tmp_path, [spec]))
        b.build_benchmark(sources, tmp_path / "bench", 10, 1, robust_dedup=False)
    assert not (tmp_path / "bench").exists()


def test_two_sources_may_not_share_photographs(tmp_path):
    root = _write_non_plant(tmp_path / "np")
    cfg = _write_config(tmp_path, [
        _first_party(root, "a"), _first_party(root / "s1", "b", layout="condition")])
    sources, _ = b.load_config(cfg)
    with pytest.raises(RuntimeError, match="share or nest roots"):
        b.build_benchmark(sources, tmp_path / "bench", 10, 1, robust_dedup=False)


def _family_csv(path, root, rows, method):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source_relative_path", "capture_family_id", "family_method"])
        for p, family in rows:
            w.writerow([p.relative_to(root).as_posix(), family, method])
    return path


def test_claimed_families_join_rows_and_keep_the_sift_backstop(tmp_path, monkeypatch):
    """A collector's family id is a claim: it merges the frames it names, and
    the image measures still run over everything else."""
    root = _write_non_plant(tmp_path / "np")
    images = sorted(root.rglob("*.jpg"))
    mapped = [p for p in images if p.parent.name != "notes"]
    claims = [(p, "fam_pot" if p.parent.name == "empty_pot" and p.parent.parent.name == "s1"
               else f"fam_{i}") for i, p in enumerate(images)]
    fam = _family_csv(tmp_path / "fam.csv", root, claims, b.CLAIMED_FAMILY_METHOD)
    calls = []

    def fake_inliers(left, right, _cache=None):
        calls.append((left, right))
        same = left.parent == right.parent and left.parent.name == "foliage_print" \
            and left.parent.parent.name == "s2"
        return 40 if same else 0

    monkeypatch.setattr(b, "_sift_homography_inliers", fake_inliers)
    sources, _ = b.load_config(_write_config(
        tmp_path, [_first_party(root, family_manifest=str(fam))]))
    out = tmp_path / "bench"
    b.build_benchmark(sources, out, 100, 1)
    rows = _manifest(out)
    assert calls, "a claimed family id switched the SIFT backstop off"
    # 12 mapped images - 2 (one claimed family of 3) - 2 (one SIFT family of 3).
    assert len(rows) == len(mapped) - 4 == 8
    sizes = collections.Counter(int(r["source_family_size"]) for r in rows)
    assert sizes == {1: 6, 3: 2}
    assert "fam_pot" in {r["capture_family_id"] for r in rows}
    assert {r["family_method"] for r in rows} == {
        f"{b.CLAIMED_FAMILY_METHOD}+{b.EXHAUSTIVE_FAMILY_METHOD}"}


def test_claimed_family_manifest_must_cover_its_source(tmp_path):
    root = _write_non_plant(tmp_path / "np")
    some = sorted(root.rglob("*.jpg"))[:3]
    fam = _family_csv(tmp_path / "fam.csv", root, [(p, p.stem) for p in some],
                      b.CLAIMED_FAMILY_METHOD)
    sources, _ = b.load_config(_write_config(
        tmp_path, [_first_party(root, family_manifest=str(fam))]))
    with pytest.raises(RuntimeError, match="manifest misses"):
        b.build_benchmark(sources, tmp_path / "bench", 100, 1, robust_dedup=False)


def test_one_claimed_family_in_two_classes_is_an_integrity_error(tmp_path):
    """The supplement lets a family span classes; a benchmark row IS a family,
    so one plant cannot be scored as both a healthy and a scorch family."""
    spec = _plant_source(tmp_path / "plants")
    root = tmp_path / "plants"
    images = sorted(root.rglob("*.jpg"))
    twins = {root / "s1/hibiscus/healthy/0.jpg", root / "s1/hibiscus/scorch/0.jpg"}
    fam = _family_csv(tmp_path / "fam.csv", root,
                      [(p, "fam_plant7" if p in twins else f"fam_{i}")
                       for i, p in enumerate(images)], b.CLAIMED_FAMILY_METHOD)
    spec["family_manifest"] = str(fam)
    sources, _ = b.load_config(_write_config(tmp_path, [spec]))
    with pytest.raises(RuntimeError, match="cross-class duplicate-family"):
        b.build_benchmark(sources, tmp_path / "bench", 100, 1, robust_dedup=False)


def test_family_manifest_with_two_methods_is_refused(tmp_path):
    root = _write_non_plant(tmp_path / "np")
    images = sorted(root.rglob("*.jpg"))
    fam = tmp_path / "fam.csv"
    with fam.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source_relative_path", "capture_family_id", "family_method"])
        for i, p in enumerate(images):
            w.writerow([p.relative_to(root).as_posix(), i,
                        b.CLAIMED_FAMILY_METHOD if i % 2 else b.PRECOMPUTED_FAMILY_METHOD])
    with pytest.raises(RuntimeError, match="mixes methods"):
        b._load_family_manifest(fam, root.resolve())


# ── deny-lists ───────────────────────────────────────────────────────

_DENY = {"slugs": {"owner/denied"}, "folders": {"owner/partly": ("hibiscus",)},
         "benchmark_slugs": {"me/bench-v3"}, "notebook": "nb.ipynb"}


def _kaggle_source(root, slug):
    return {"id": "k1", "root": str(root), "origin": "kaggle", "slug": slug,
            "version": "3", "layout": "species/condition", "license_id": "CC0-1.0",
            "conditions": {"healthy": {"class": "healthy"}}}


def _write_kaggle(root, species=("Hibiscus", "rose")):
    seed = 5000
    for sp in species:
        for i in range(2):
            seed += 1
            _img(root / sp / "healthy" / f"{i}.jpg", seed)
    return root


def test_deny_lists_are_read_from_the_notebook_not_copied():
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    deny = b.load_deny_lists(NOTEBOOK)
    assert b.SOURCE_DATASET_SLUG in deny["slugs"], \
        "the v2 benchmark's own source is no longer deny-listed for training"
    assert deny["folders"]["aditisalvi04/indoor-plant-disease-dataset"] == ("hibiscus",)
    assert "maxayyt5/plantvision-ood-benchmark" in deny["benchmark_slugs"]


def test_legacy_build_passes_the_real_deny_lists(src, tmp_path):
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    out = tmp_path / "bench"
    b.build(src, out, 100, 1, robust_dedup=False, deny_lists=b.load_deny_lists(NOTEBOOK))
    assert "KAGGLE_DENY_SLUGS" in json.loads(
        (out / "build_info.json").read_text())["deny_list_check"][0]


@pytest.mark.parametrize("slug, species, publish, error", [
    ("owner/denied", ("Hibiscus", "rose"), "", None),
    ("owner/denied", ("Hibiscus", "rose"), "me/bench-v3", None),
    ("owner/partly", ("Hibiscus",), "", None),
    ("owner/partly", ("Hibiscus", "rose"), "", "fall outside its KAGGLE_DENY_FOLDERS"),
    ("owner/fresh", ("Hibiscus",), "", "owner/fresh .* is not deny-listed"),
    ("owner/denied", ("Hibiscus",), "me/unlisted", "not in KAGGLE_BENCHMARK_SLUGS"),
])
def test_an_undenied_source_refuses_the_build(tmp_path, slug, species, publish, error):
    root = _write_kaggle(tmp_path / "k", species)
    sources, _ = b.load_config(_write_config(tmp_path, [_kaggle_source(root, slug)]))
    out = tmp_path / "bench"

    def run():
        b.build_benchmark(sources, out, 100, 1, robust_dedup=False,
                          deny_lists=_DENY, benchmark_slug=publish)
    if error is None:
        run()
        assert {r["source_slug"] for r in _manifest(out)} == {slug}
    else:
        with pytest.raises(RuntimeError, match=error):
            run()
        assert not out.exists(), "a refused build must write nothing"


def test_missing_notebook_refuses_rather_than_skipping_the_check(tmp_path):
    with pytest.raises(RuntimeError, match="training notebook not found"):
        b.load_deny_lists(tmp_path / "nope.ipynb")


def test_cli_builds_from_a_config(tmp_path, monkeypatch, capsys):
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    cfg = _write_config(tmp_path, [_first_party(_write_non_plant(tmp_path / "np"))])
    out = tmp_path / "bench"
    monkeypatch.setattr(b, "_sift_homography_inliers", lambda *_a, **_k: 0)
    monkeypatch.setattr("sys.argv", ["build_ood_benchmark", "--config", str(cfg),
                                     "--out", str(out), "--min-clusters-per-class", "5"])
    b.main()
    rows = _manifest(out)
    assert len(rows) == 12
    assert {r["family_method"] for r in rows} == {b.EXHAUSTIVE_FAMILY_METHOD}
    printed = capsys.readouterr().out
    assert "deny-list: np01: first_party batch" in printed
    assert "v3 target: not_a_plant[non_plant]: 12 families" in printed
    # The default floor still refuses a build this thin.
    monkeypatch.setattr("sys.argv", ["build_ood_benchmark", "--config", str(cfg),
                                     "--out", str(tmp_path / "thin")])
    with pytest.raises(RuntimeError, match="under 60 independent capture families"):
        b.main()
    monkeypatch.setattr("sys.argv", ["build_ood_benchmark", "--config", str(cfg),
                                     "--src", str(tmp_path / "np")])
    with pytest.raises(SystemExit, match="exactly one of"):
        b.main()


# ── notebook §6 reads what the tool writes ───────────────────────────


def _notebook_cell(marker):
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    cells = ["".join(c["source"]) for c in json.loads(NOTEBOOK.read_text())["cells"]]
    return next(s for s in cells if marker in s)


def _run_section6(bench, *, require=False, slug="maxayyt5/plantvision-ood-benchmark",
                  wrong=()):
    """Exec the notebook's own §2 deny block and §6 benchmark block on `bench`.

    The model is a stub that predicts every row's own class, except rows whose
    relative path is in ``wrong`` ({path: predicted class})."""
    names = b.load_class_names()
    c2 = _notebook_cell("KAGGLE_DENY_FOLDERS = {")
    start = c2.index("KAGGLE_DENY_FOLDERS = {")
    ns: dict = {}
    exec(compile(c2[start:c2.index("    return True", start) + len("    return True")],
                 "<deny-list>", "exec"), ns)
    fetched = []

    def kaggle_download(s):
        ns["_assert_source_allowed"](s)
        fetched.append(s)
        return bench

    def make_eval_loader(root, _img_size, class_to_idx=None):
        samples = [(str(p), class_to_idx[p.parent.name])
                   for p in sorted(Path(root).rglob("*.jpg"))]
        return type("DS", (), {"samples": samples})(), samples

    def collect_logits(_sess, samples):
        y = np.array([t for _p, t in samples])
        logits = np.zeros((len(samples), len(names)), dtype=np.float32)
        for i, (p, t) in enumerate(samples):
            rel = Path(p).relative_to(bench).as_posix()
            logits[i, names.index(wrong[rel]) if rel in wrong else t] = 5.0
        return logits, y

    c6 = _notebook_cell("OOD_BENCHMARK_SLUG = '")
    block = c6[c6.index("OOD_BENCHMARK_SLUG = '"):c6.index("model_card['ood_benchmark']")]
    for old, new in (
            ("REQUIRE_OOD_BENCHMARK = False", f"REQUIRE_OOD_BENCHMARK = {require}"),
            ("OOD_BENCHMARK_SLUG = 'maxayyt5/plantvision-ood-benchmark'",
             f"OOD_BENCHMARK_SLUG = {slug!r}")):
        assert block.count(old) == 1, old
        block = block.replace(old, new)
    ns.update({
        "_kaggle_download": kaggle_download, "make_eval_loader": make_eval_loader,
        "_collect_logits": collect_logits, "eval_sess": None, "IMG": 224,
        "TRAIN_CLASS_TO_IDX": {n: i for i, n in enumerate(names)}, "CLASS_NAMES": names,
        "shipped_artifact": "stub.onnx", "log_run": lambda _d: None,
        "Path": Path, "PurePosixPath": PurePosixPath, "np": np})
    exec(compile(block, "<notebook §6 ood block>", "exec"), ns)
    assert fetched == [slug]
    return ns


def test_section6_scores_a_tool_built_v3_without_edits(v3, capsys):
    rows = _manifest(v3)
    pot = next(r["filename"] for r in rows if r["negative_kind"] == "empty_pot")
    leaf = next(r["filename"] for r in rows if r["negative_kind"] == "variegated")
    scorch = next(r["filename"] for r in rows if r["species"] == "hibiscus"
                  and r["class"] == "leaf_scorch_underwatering")
    ns = _run_section6(v3, require=True, wrong={
        pot: "healthy", leaf: "not_a_plant", scorch: "healthy"})
    got = ns["ood_benchmark"]
    assert got["independent_family_count_verified"] is True
    assert got["nominal_n"] == len(rows) and got["unlisted_images"] == 0
    assert got["undenied_source_slugs"] == []
    assert got["scorch_recall_by_arm"]["hibiscus"] == {"n": 4, "recall": 0.75}
    assert got["healthy_n"] == 24 and got["healthy_false_fire"] == 0.0
    assert got["non_plant_n"] == 12
    kinds = got["non_plant_accepted_as_plant_by_kind"]
    assert kinds["empty_pot"]["n"] == 6
    assert kinds["empty_pot"]["accepted_as_plant"] == pytest.approx(1 / 6)
    assert kinds["foliage_print"]["accepted_as_plant"] == 0.0
    assert got["difficult_plant_by_kind"]["variegated"] == {
        "n": 12, "false_reject": pytest.approx(1 / 12), "false_fire": 0.0}
    assert got["alerts"] == []
    assert "non-plant[empty_pot" in capsys.readouterr().out
    # The fetch permission is scoped to the one call.
    assert ns["_BENCHMARK_FETCH"] == {"slug": None}
    with pytest.raises(RuntimeError, match="BENCHMARK CONTAMINATION"):
        ns["_assert_source_allowed"]("maxayyt5/plantvision-ood-benchmark")


def test_section6_scores_a_non_plant_only_benchmark(tmp_path):
    """The first v3 revision may hold the non-plant arm alone: no scorch rows
    must not put NaN into model_card.json."""
    sources, _ = b.load_config(_write_config(
        tmp_path, [_first_party(_write_non_plant(tmp_path / "np"))]))
    out = tmp_path / "bench"
    b.build_benchmark(sources, out, 100, 1, robust_dedup=False)
    got = _run_section6(out)["ood_benchmark"]
    assert got["scorch_recall_pooled"] is None and got["scorch_recall_by_arm"] == {}
    assert got["non_plant_n"] == 12
    assert "NaN" not in json.dumps(got)
    assert any("missing from the benchmark" in a for a in got["alerts"])


def test_section6_refuses_to_gate_on_an_undenied_source_or_unlisted_image(tmp_path):
    root = _write_kaggle(tmp_path / "k")
    sources, _ = b.load_config(_write_config(tmp_path, [_kaggle_source(root, "owner/fresh")]))
    out = tmp_path / "bench"
    b.build_benchmark(sources, out, 100, 1, robust_dedup=False)
    assert _run_section6(out)["ood_benchmark"]["undenied_source_slugs"] == ["owner/fresh"]
    with pytest.raises(RuntimeError, match="could train on them"):
        _run_section6(out, require=True)

    out2 = tmp_path / "bench2"
    b.build(_write_source(tmp_path / "flower"), out2, 100, 1, robust_dedup=False)
    _img(out2 / "healthy" / "stray.jpg", 77)
    assert _run_section6(out2)["ood_benchmark"]["unlisted_images"] == 1
    with pytest.raises(RuntimeError, match="no manifest row"):
        _run_section6(out2, require=True)


def test_section6_will_not_download_a_benchmark_nothing_denies(v3):
    with pytest.raises(RuntimeError, match="not in KAGGLE_BENCHMARK_SLUGS"):
        _run_section6(v3, slug="me/new-bench")


# ── report() ─────────────────────────────────────────────────────────


def _report_fixture(tmp_path, with_family=True):
    """healthy: rose plain + variegated difficult; scorch: two arms, three
    species; not_a_plant: two kinds. Model a is perfect."""
    names = ["healthy", "leaf_scorch_underwatering", "leaf_spot", "not_a_plant"]
    spec = [  # (class, species, arm, negative_kind, n, y_pred_b)
        ("healthy", "rose", "", "", 4, [0, 0, 1, -1]),
        ("healthy", "pothos", "difficult_plant", "variegated", 4, [0, 3, 3, 2]),
        ("leaf_scorch_underwatering", "hibiscus", "necrosis", "", 4, [1, 1, 1, 1]),
        ("leaf_scorch_underwatering", "ficus", "necrosis", "", 4, [1, 1, 0, 0]),
        ("leaf_scorch_underwatering", "banana_bush", "turgor", "", 2, [0, 0]),
        ("not_a_plant", "", "non_plant", "empty_pot", 4, [3, -1, 0, 1]),
        ("not_a_plant", "", "non_plant", "foliage_print", 2, [3, 3]),
    ]
    rows, items, y_true, y_b = [], [], [], []
    for cls, species, arm, kind, n, preds in spec:
        for i in range(n):
            name = f"{cls}/{species or kind}__{i}.jpg"
            rows.append({"filename": name, "class": cls, "species": species, "arm": arm,
                         "negative_kind": kind, "capture_family_id": f"f{len(rows)}"})
            items.append(name)
            y_true.append(names.index(cls))
        y_b.extend(preds)
    fields = ["filename", "class", "species", "arm", "negative_kind"]
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields + (["capture_family_id"] if with_family else []),
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    preds = tmp_path / "preds.json"
    preds.write_text(json.dumps({
        "class_names": names, "items": items, "y_true": y_true,
        "y_pred_a": list(y_true), "y_pred_b": y_b, "label_a": "old", "label_b": "new"}))
    return preds, manifest


def _stat(result, cls, arm, unit, stat):
    hits = [r for r in result["rows"]
            if (r["class"], r["arm"], r["unit"], r["stat"]) == (cls, arm, unit, stat)]
    assert len(hits) == 1, (cls, arm, unit, stat, hits)
    return hits[0]


def test_report_scores_the_non_plant_arm_by_what_was_accepted(tmp_path, capsys):
    """On a non-plant an abstention is the RIGHT outcome; recall of
    `not_a_plant` alone counts it as a miss."""
    result = b.report(*_report_fixture(tmp_path))
    out = capsys.readouterr().out
    pot = _stat(result, "not_a_plant", "non_plant", "empty_pot", "recall")
    assert (pot["a"], pot["b"], pot["n"]) == (1.0, 0.25, 4)
    accepted = _stat(result, "not_a_plant", "non_plant", "empty_pot", "accepted_as_plant")
    assert (accepted["a"], accepted["b"]) == (0.0, 0.5), \
        "the abstention was counted as accepting the pot as a plant"
    assert _stat(result, "not_a_plant", "*", "*", "accepted_as_plant")["b"] == pytest.approx(2 / 6)
    assert _stat(result, "not_a_plant", "non_plant", "foliage_print",
                 "accepted_as_plant")["b"] == 0.0
    line = next(ln.split() for ln in out.splitlines()
                if ln.startswith("not_a_plant ACCEPTED AS PLANT"))
    assert line[-4:] == ["0.000", "0.333", "+0.333", "6"]
    assert "WARN" not in out
    assert result["family_verified"] is True


def test_report_splits_healthy_into_plain_and_difficult_plant(tmp_path, capsys):
    result = b.report(*_report_fixture(tmp_path))
    out = capsys.readouterr().out
    assert _stat(result, "healthy", "*", "*", "recall")["b"] == pytest.approx(3 / 8)
    assert "healthy" in next(ln for ln in out.splitlines() if "POOLED" in ln)
    # plain rose: one disease call, one abstention.
    assert _stat(result, "healthy", "", "rose", "false_fire")["b"] == 0.25
    assert _stat(result, "healthy", "", "rose", "false_reject")["b"] == 0.25
    # difficult plants: two turned away as not_a_plant, one called diseased.
    hard = ("healthy", "difficult_plant", "variegated")
    assert _stat(result, *hard, "false_reject")["b"] == 0.5
    assert _stat(result, *hard, "false_fire")["b"] == 0.25
    assert _stat(result, *hard, "recall")["b"] == 0.25
    assert _stat(result, "healthy", "*", "*", "false_reject")["b"] == pytest.approx(3 / 8)
    assert _stat(result, "healthy", "*", "*", "false_fire")["b"] == 0.25
    assert "[plain]" in out and "[difficult_plant]" in out


def test_report_keeps_scorch_arms_apart_and_sizes_the_species_interval(tmp_path, capsys):
    result = b.report(*_report_fixture(tmp_path))
    out = capsys.readouterr().out
    scorch = "leaf_scorch_underwatering"
    assert _stat(result, scorch, "*", "*", "recall")["b"] == 0.6
    assert _stat(result, scorch, "necrosis", "*", "recall")["b"] == 0.75
    assert _stat(result, scorch, "turgor", "*", "recall")["b"] == 0.0
    assert _stat(result, scorch, "necrosis", "ficus", "recall")["b"] == 0.5
    assert "not a release gate" in out and "off-morphology probe" in out
    # Model a is perfect on both necrosis species: SD 0, k=2. The turgor arm
    # has one species and gets no interval; nor does a class with no species.
    half = _stat(result, scorch, "necrosis", "*", "between_species_half_width")
    assert (half["a"], half["n"]) == (0.0, 2)
    assert not [r for r in result["rows"] if r["stat"] == "between_species_half_width"
                and (r["arm"] == "turgor" or r["class"] == "not_a_plant")]
    assert "leaf_scorch_underwatering[necrosis]" in out


def test_report_between_species_interval_uses_student_t(tmp_path, capsys):
    names = ["healthy", "leaf_scorch_underwatering"]
    items = [f"leaf_scorch_underwatering/{sp}__{i}.jpg" for sp in ("a", "b") for i in range(4)]
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "class", "species", "arm"])
        w.writeheader()
        w.writerows({"filename": it, "class": names[1],
                     "species": it.split("/")[1][0], "arm": "necrosis"} for it in items)
    preds = tmp_path / "preds.json"
    preds.write_text(json.dumps({"class_names": names, "items": items, "y_true": [1] * 8,
                                 "y_pred_a": [1, 1, 1, 1, 0, 0, 0, 0]}))
    result = b.report(preds, manifest)
    out = capsys.readouterr().out
    sd = float(np.std([1.0, 0.0], ddof=1))
    half = _stat(result, names[1], "necrosis", "*", "between_species_half_width")["a"]
    assert half == pytest.approx(12.706 * sd / np.sqrt(2))
    assert "TOO FEW SPECIES for a domain claim" in out
    assert f"needs k~{int(np.ceil((1.96 * sd / 0.15) ** 2))} species" in out
    assert "NOMINAL IMAGE count" in out, "no capture_family_id column and no warning"
    assert result["family_verified"] is False


def test_report_refuses_a_manifest_that_does_not_list_a_scored_image(tmp_path):
    preds, manifest = _report_fixture(tmp_path)
    d = json.loads(preds.read_text())
    d["items"][0] = "healthy/never_listed.jpg"
    preds.write_text(json.dumps(d))
    with pytest.raises(SystemExit, match="1 scored image.s. have no row"):
        b.report(preds, manifest)


def test_report_flags_repeated_families_as_nominal_counts(tmp_path, capsys):
    preds, manifest = _report_fixture(tmp_path)
    manifest.write_text(manifest.read_text().replace(",f1\n", ",f0\n"))
    assert b.report(preds, manifest)["family_verified"] is False
    assert "NOMINAL IMAGE count" in capsys.readouterr().out


def test_report_on_a_tool_built_manifest_joins_every_row(v3, tmp_path, capsys):
    """End to end: the columns build() writes are the ones report() reads."""
    names = b.load_class_names()
    rows = _manifest(v3)
    preds = tmp_path / "preds.json"
    y = [names.index(r["class"]) for r in rows]
    preds.write_text(json.dumps({
        "class_names": names, "items": [r["filename"] for r in rows], "y_true": y,
        "y_pred_a": y, "y_pred_b": [-1] * len(y)}))
    result = b.report(preds, v3 / "manifest.csv")
    assert result["family_verified"] is True
    assert _stat(result, "not_a_plant", "non_plant", "empty_pot", "accepted_as_plant")["n"] == 6
    assert _stat(result, "healthy", "difficult_plant", "variegated", "false_reject")["b"] == 1.0
    assert _stat(result, "leaf_scorch_underwatering", "unassigned", "pothos", "recall")["n"] == 4
    assert "[unassigned]" in capsys.readouterr().out
