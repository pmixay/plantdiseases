"""Tests for the paired A/B eval harness (``tools/paired_eval.py``).

The join and the filter are the whole trust surface here: if a supplement row
fails to match the file it became, it is silently dropped from the comparison;
if a non-supplement row survives the filter, the incumbent gets scored on
images it trained on. Both failures produce a plausible-looking number, so
they are tested directly rather than eyeballed.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from tools.paired_eval import (  # noqa: E402
    _manifest_provenance,
    _sanitise_stem,
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
