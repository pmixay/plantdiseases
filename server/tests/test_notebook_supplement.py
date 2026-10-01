"""Behaviour of the notebook's supplement loader and Stage 1 dataset build.

These execute the notebook's own §2 / §3 code against synthetic folders (the
notebook cannot be imported: only it ships to Colab). They pin what the string
checks in ``test_data_invariants.py`` cannot:

* a v8-shaped manifest (no provenance columns) loads — 2.7.5 made the columns
  optional, and a family check keyed on ``None`` would have aborted §2 on the
  second eval-eligible row;
* light collection: an image without a manifest row loads train-only, a capture
  family routes as one unit, a reused source id is skipped — none of it stops
  the run;
* YOLO box files beside a photo reach the box index without decoding anything,
  in the EXIF-rotated frame a browser labelling tool shows;
* the pooled PlantDoc -> YOLO build writes byte-identical files, and draws the
  identical seeded train/val split, as the serial loop it replaced, and decodes
  only rows that carry a box.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

import pytest
import yaml
from PIL import Image

NOTEBOOK = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"
CLASSES = ["healthy", "leaf_spot", "not_a_plant"]


def _cell(marker: str) -> str:
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    for cell in json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]:
        src = "".join(cell["source"])
        if marker in src:
            return src
    pytest.fail(f"no notebook cell contains {marker!r} — was it renamed?")


def _between(src: str, start: str, end: str) -> str:
    return src[src.index(start):src.index(end, src.index(start))]


@dataclass
class _Ann:  # the CropAnnotation fields the loader writes
    plant_id: str
    species: str | None
    diagnosis: str
    severity: str
    leaf_or_whole_plant: str
    capture_session: str
    source: str
    label_confidence: float
    bbox: list
    subject_domain: str | None = None
    license_id: str | None = None
    license_url: str | None = None
    creator: str | None = None
    source_url: str | None = None
    source_item_id: str | None = None
    attribution: str | None = None
    immutable_source_id: str | None = None
    capture_family_id: str | None = None
    first_seen_model_version: str | None = None


def _tqdm(it=None, **_kw):
    if it is not None:
        return it

    class _Bar:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def update(self, _n):
            pass

    return _Bar()


def _base_ns(tmp_path: Path) -> dict:
    s2 = _cell("def fetch_kaggle_supplement(")
    root = tmp_path / "data_v2"
    seen: set[str] = set()

    def _claim_sha(body: bytes) -> bool:
        h = hashlib.sha256(body).hexdigest()
        if h in seen:
            return False
        seen.add(h)
        return True

    def write_sidecar(p: Path, ann) -> None:
        p.with_suffix(".json").write_text(json.dumps(asdict(ann)))

    ns = {
        "MODEL_VERSION": "2.7.5", "ROOT": root, "TRAIN_ROOT": root / "train",
        "CLASSES": CLASSES, "IMG_EXTS": {".jpg", ".jpeg", ".png", ".webp", ".bmp"},
        "re": re, "math": math, "json": json, "hashlib": hashlib, "os": os,
        "shutil": shutil, "random": random, "Path": Path, "PurePosixPath": PurePosixPath,
        "PILImage": Image, "BytesIO": io.BytesIO, "ThreadPoolExecutor": ThreadPoolExecutor,
        "tqdm": _tqdm, "yaml": yaml, "Optional": Optional,
        "_claim_sha": _claim_sha, "write_sidecar": write_sidecar, "CropAnnotation": _Ann,
        "KAGGLE_SUPPLEMENT_SLUG": "owner/supplement",
    }
    for start, end in (("def _save_rgb(", "def _claim_sha("),
                       ("def _save_archive_member(", "\n# ── Curated abiotic-stress supplement #2"),
                       ("def _hf_lazy_images(", "def _hf_load_with_retry("),
                       ("STRICT_SUPPLEMENT_SPLIT_HINT = True", "\n# ── Run the pipeline")):
        exec(compile(_between(s2, start, end), f"<notebook §2 {start}>", "exec"), ns)  # noqa: S102
    return ns


def _photo(path: Path, seed: int, size=(240, 200), exif_orientation: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    img.putpixel((0, 0), (seed % 256, (seed * 7) % 256, 3))   # distinct bytes per photo
    kw = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kw["exif"] = exif.tobytes()
    img.save(path, format="JPEG", quality=90, **kw)


def _supplement(tmp_path: Path, rows: list[dict], extra_files=()) -> Path:
    """Build <tmp>/supp/<class>/<file> + manifest.csv. Every row gets a photo."""
    supp = tmp_path / "supp"
    for k, r in enumerate(rows):
        _photo(supp / r["filename"], seed=k + 1, **r.pop("_photo", {}))
    for k, rel in enumerate(extra_files):
        _photo(supp / rel, seed=1000 + k)
    cols = sorted({c for r in rows for c in r})
    with (supp / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return supp


def _v8_rows(n_eval: int = 6) -> list[dict]:
    """v8 shape: class/filename/hn_kind/split_hint/confidence, NO provenance."""
    rows = []
    for k in range(n_eval):
        cls = CLASSES[k % 2]
        rows.append({"filename": f"{cls}/img_{k:03d}.jpg", "class": cls, "hn_kind": "",
                     "split_hint": "eval_ok", "confidence": "0.95"})
    rows.append({"filename": "not_a_plant/bnd_000.jpg", "class": "not_a_plant",
                 "hn_kind": "boundary", "split_hint": "train_only", "confidence": "0.9"})
    rows.append({"filename": "healthy/tr_000.jpg", "class": "healthy", "hn_kind": "",
                 "split_hint": "train_only", "confidence": "0.6"})
    return rows


def _run(ns: dict, supp: Path) -> dict[str, dict]:
    ns["SUPPLEMENT_MIN_ROWS"] = 0
    ns["_kaggle_download"] = lambda slug: supp
    ns["fetch_kaggle_supplement"](per_class_cap=None)
    out = {}
    for p in sorted((ns["TRAIN_ROOT"]).rglob("*.json")):
        out[p.stem] = json.loads(p.read_text())
    return out


def _sources(saved: dict[str, dict]) -> dict[str, str]:
    return {k: v["source"] for k, v in saved.items()}


def test_v8_manifest_without_provenance_loads(tmp_path):
    """Regression: blank capture_family_id must not be a shared family key."""
    ns = _base_ns(tmp_path)
    saved = _run(ns, _supplement(tmp_path, _v8_rows(n_eval=6)))
    src = _sources(saved)
    assert sum(1 for s in src.values() if s == "supplement") == 6
    assert src["supp_not_a_plant_bnd_000"] == "supplement_boundary"
    assert src["supp_healthy_tr_000"] == "supplement_train_only"
    # Holdout keying is unchanged for v8: plant_id is the path-derived stem.
    assert saved["supp_healthy_img_000"]["plant_id"] == "supp_healthy_img_000"
    assert json.loads(ns["SUPPLEMENT_BOXES_INDEX"].read_text()) == {}


def test_unlisted_images_load_train_only(tmp_path):
    ns = _base_ns(tmp_path)
    supp = _supplement(tmp_path, _v8_rows(), extra_files=["leaf_spot/new_scan_1.jpg",
                                                          "healthy/sub/new_scan_2.jpg"])
    src = _sources(_run(ns, supp))
    assert src["supp_leaf_spot_new_scan_1"] == "supplement_train_only"
    assert src["supp_healthy_sub_new_scan_2"] == "supplement_train_only"
    assert ns["_SUPPLEMENT_PROVENANCE_RESOLVED"]["unlisted images loaded train-only"] == 2

    ns2 = _base_ns(tmp_path / "strict")
    ns2["SUPPLEMENT_LOAD_UNLISTED_IMAGES"] = False
    with pytest.raises(RuntimeError, match="has no manifest row"):
        _run(ns2, supp)


def test_unlisted_images_never_become_eval_even_without_a_routing_basis(tmp_path):
    ns = _base_ns(tmp_path)
    rows = [{k: v for k, v in r.items() if k not in ("split_hint", "confidence")}
            for r in _v8_rows()]
    ns["STRICT_SUPPLEMENT_SPLIT_HINT"] = False
    supp = _supplement(tmp_path, rows, extra_files=["leaf_spot/new_scan.jpg"])
    src = _sources(_run(ns, supp))
    assert src["supp_leaf_spot_img_001"] == "supplement"          # basis 'none': listed rows eval
    assert src["supp_leaf_spot_new_scan"] == "supplement_train_only"


def test_capture_family_routes_as_one_unit(tmp_path):
    ns = _base_ns(tmp_path)
    rows = _v8_rows(n_eval=2)
    rows += [
        # family A: eval row + train-only sibling -> the whole family trains
        {"filename": "leaf_spot/a1.jpg", "class": "leaf_spot", "split_hint": "eval_ok",
         "confidence": "0.9", "capture_family_id": "famA"},
        {"filename": "leaf_spot/a2.jpg", "class": "leaf_spot", "split_hint": "train_only",
         "confidence": "0.9", "capture_family_id": "famA"},
        # family B: two eval rows across classes -> both stay eval, one group
        {"filename": "leaf_spot/b1.jpg", "class": "leaf_spot", "split_hint": "eval_ok",
         "confidence": "0.9", "capture_family_id": "famB"},
        {"filename": "healthy/b2.jpg", "class": "healthy", "split_hint": "eval_ok",
         "confidence": "0.9", "capture_family_id": "famB"},
        # family C: one member already trained by 2.7.2 -> the family is seen
        {"filename": "healthy/c1.jpg", "class": "healthy", "split_hint": "eval_ok",
         "confidence": "0.9", "capture_family_id": "famC", "first_seen_model_version": "2.7.2"},
        {"filename": "healthy/c2.jpg", "class": "healthy", "split_hint": "eval_ok",
         "confidence": "0.9", "capture_family_id": "famC", "first_seen_model_version": "never"},
    ]
    saved = _run(ns, _supplement(tmp_path, rows))
    src = _sources(saved)
    assert src["supp_leaf_spot_a1"] == src["supp_leaf_spot_a2"] == "supplement_train_only"
    assert src["supp_leaf_spot_b1"] == src["supp_healthy_b2"] == "supplement"
    assert saved["supp_leaf_spot_b1"]["plant_id"] == saved["supp_healthy_b2"]["plant_id"] == "famB"
    assert src["supp_healthy_c1"] == src["supp_healthy_c2"] == "supplement_train_only"


def test_reused_source_id_is_skipped_not_fatal(tmp_path):
    ns = _base_ns(tmp_path)
    rows = _v8_rows(n_eval=2)
    rows += [{"filename": "leaf_spot/x1.jpg", "class": "leaf_spot", "split_hint": "train_only",
              "immutable_source_id": "inat:42"},
             {"filename": "leaf_spot/x2.jpg", "class": "leaf_spot", "split_hint": "train_only",
              "immutable_source_id": "inat:42"}]
    saved = _run(ns, _supplement(tmp_path, rows))
    assert "supp_leaf_spot_x1" in saved and "supp_leaf_spot_x2" not in saved
    assert ns["_SUPPLEMENT_PROVENANCE_RESOLVED"]["duplicate immutable_source_id skipped"] == 1


def test_yolo_boxes_reach_the_index_in_the_displayed_frame(tmp_path):
    ns = _base_ns(tmp_path)
    rows = _v8_rows(n_eval=2)
    rows += [{"filename": "leaf_spot/boxed.jpg", "class": "leaf_spot",
              "split_hint": "train_only", "capture_family_id": "famBox",
              "_photo": {"size": (300, 200), "exif_orientation": 6}}]
    supp = _supplement(tmp_path, rows)
    (supp / "leaf_spot" / "boxed.txt").write_text(
        "0 0.5 0.5 0.4 0.3\n"            # a box
        "3 0.1 0.1 0.3 0.1 0.3 0.3\n"   # a polygon, any class id
        "0 120 80 40 30\n"              # pixel coordinates: dropped
        "garbage\n")
    (supp / "healthy" / "img_000.txt").write_text("")   # empty file: no box, no entry
    _run(ns, supp)
    index = json.loads(ns["SUPPLEMENT_BOXES_INDEX"].read_text())
    assert set(index) == {"leaf_spot/supp_leaf_spot_boxed.jpg"}
    entry = index["leaf_spot/supp_leaf_spot_boxed.jpg"]
    assert entry["group"] == "famBox"
    assert entry["boxes"] == [[0.5, 0.5, 0.4, 0.3], [0.2, 0.2, 0.2, 0.2]]
    # EXIF orientation 6 (rotate 90): the stored photo is the upright 200x300 frame.
    with Image.open(ns["TRAIN_ROOT"] / "leaf_spot" / "supp_leaf_spot_boxed.jpg") as im:
        assert im.size == (200, 300)
    # An unboxed photo keeps the stored-pixel frame it always had (v8 parity).
    with Image.open(ns["TRAIN_ROOT"] / "healthy" / "supp_healthy_img_000.jpg") as im:
        assert im.size == (240, 200)


@pytest.mark.parametrize("text,boxes,bad", [
    ("0 0.5 0.5 0.2 0.2", [(0.5, 0.5, 0.2, 0.2)], 0),
    ("0 0.5 0.5 0.2 0.2 0.87", [(0.5, 0.5, 0.2, 0.2)], 0),          # trailing confidence
    ("0 0.95 0.5 0.2 0.2", [(0.925, 0.5, 0.15, 0.2)], 0),           # clipped to the frame
    ("0 0.5 0.5 0.001 0.2", [], 1),                                  # degenerate
    ("0 310 220 64 48", [], 1),                                      # pixel coordinates
    ("0 nan 0.5 0.2 0.2", [], 1),
    ("0 0.5 0.5", [], 1),
    ("\n\n", [], 0),
])
def test_parse_yolo_boxes(tmp_path, text, boxes, bad):
    ns = _base_ns(tmp_path)
    got, got_bad = ns["_parse_yolo_boxes"](text)
    assert got == pytest.approx(boxes) and got_bad == bad


# ── §3: the pooled PlantDoc -> YOLO build ──────────────────────────────────


class _HFImage:
    """Stand-in for ``datasets.Image`` that counts decodes."""

    decodes = 0

    def __init__(self, decode: bool = True):
        self.decode = decode

    def decode_example(self, value):
        type(self).decodes += 1
        return Image.open(io.BytesIO(value["bytes"]))


class _Split:
    def __init__(self, rows, feature):
        self._rows, self.features = rows, {"image": feature}

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, i):
        row = dict(self._rows[i])
        if self.features["image"].decode:
            row["image"] = self.features["image"].decode_example(row["image"])
        return row

    def cast_column(self, column, feature):
        return _Split(self._rows, feature)


def _plantdoc(n: int = 40) -> dict:
    rnd = random.Random(4)
    out = {}
    for split in ("train", "test"):
        rows = []
        for k in range(n):
            w, h = rnd.randrange(200, 500), rnd.randrange(200, 500)
            buf = io.BytesIO()
            Image.new("RGB", (w, h), (rnd.randrange(256), 90, k)).save(buf, format="JPEG")
            boxes = [] if k % 7 == 0 else [
                [rnd.uniform(0, w / 2), rnd.uniform(0, h / 2),
                 rnd.uniform(-1, w / 2), rnd.uniform(-1, h / 2)] for _ in range(rnd.randrange(1, 4))]
            rows.append({"image": {"bytes": buf.getvalue(), "path": None},
                         "image_id": f"{split}{k}", "objects": {"bbox": boxes}})
        out[split] = _Split(rows, _HFImage())
    return out


def _serial_reference(ds, out_root: Path, save_rgb, train_ratio=0.85, seed=7) -> None:
    """The pre-2.7.5 loop, verbatim in substance: decode every row, serially."""
    rng = random.Random(seed)
    for split_name in ("train", "test"):
        split = ds[split_name]
        order = list(range(len(split)))
        rng.shuffle(order)
        for i in order:
            row = split[i]
            bboxes = (row.get("objects") or {}).get("bbox") or []
            if not bboxes:
                continue
            img = row["image"].convert("RGB")
            w, h = img.size
            lines = []
            for bbox in bboxes:
                x, y, bw, bh = (float(v) for v in bbox)
                if bw <= 1 or bh <= 1:
                    continue
                cx, cy, nw, nh = (x + bw / 2) / w, (y + bh / 2) / h, bw / w, bh / h
                if not (0 < cx < 1 and 0 < cy < 1 and 0 < nw <= 1 and 0 < nh <= 1):
                    continue
                lines.append(f"0 {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            if not lines:
                continue
            target = "val" if (split_name == "test" or rng.random() > train_ratio) else "train"
            stem = f"plantdoc_{split_name}_{row.get('image_id', i)}"
            img_path = out_root / "images" / target / f"{stem}.jpg"
            if img_path.exists():
                continue
            img_path.parent.mkdir(parents=True, exist_ok=True)
            save_rgb(img, img_path)
            lbl = out_root / "labels" / target / f"{stem}.txt"
            lbl.parent.mkdir(parents=True, exist_ok=True)
            lbl.write_text("\n".join(lines) + "\n")


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in (".jpg", ".txt")}


def _detector_ns(tmp_path: Path, ds) -> dict:
    ns = _base_ns(tmp_path)
    sys.modules["datasets"] = types.SimpleNamespace(Image=_HFImage)
    s3 = _cell("def build_yolo_detection_dataset_from_plantdoc(")
    ns["DETECTOR_DATA_ROOT"] = tmp_path / "data_v2" / "detector"
    ns["_hf_load_with_retry"] = lambda name: ds
    ns["iter_images"] = lambda root: (p for p in sorted(Path(root).rglob("*.jpg")))
    exec(compile(_between(s3, "def _link_or_copy(", "yaml_path = find_detector_yaml()"),  # noqa: S102
                 "<notebook §3 detector dataset>", "exec"), ns)
    return ns


@pytest.fixture
def _fake_datasets_module():
    saved = sys.modules.get("datasets")
    yield
    if saved is None:
        sys.modules.pop("datasets", None)
    else:
        sys.modules["datasets"] = saved


def test_pooled_yolo_build_is_byte_identical_to_the_serial_loop(tmp_path, _fake_datasets_module):
    ds = _plantdoc()
    ns = _detector_ns(tmp_path, ds)
    ns["TRAIN_ROOT"].mkdir(parents=True)          # no not_a_plant -> no backgrounds
    _HFImage.decodes = 0
    yaml_path = ns["build_yolo_detection_dataset_from_plantdoc"]()
    assert yaml_path is not None
    boxed_rows = sum(1 for s in ds.values() for r in s._rows if r["objects"]["bbox"])
    assert _HFImage.decodes == boxed_rows, "box-less rows must not be decoded"

    ref = tmp_path / "reference"
    _serial_reference(ds, ref, ns["_save_rgb"])
    got = _tree(ns["DETECTOR_DATA_ROOT"])
    want = _tree(ref)
    assert got.keys() == want.keys(), "train/val draw differs from the serial loop"
    assert got == want, "image or label bytes differ from the serial loop"


def test_supplement_boxes_join_the_detector_set_only_from_train(tmp_path, _fake_datasets_module):
    ds = _plantdoc(n=12)
    ns = _detector_ns(tmp_path, ds)
    train = ns["TRAIN_ROOT"]
    for k in range(30):
        _photo(train / "leaf_spot" / f"supp_leaf_spot_{k}.jpg", seed=k)
    _photo(train / "not_a_plant" / "bg_source.jpg", seed=99)
    index = {f"leaf_spot/supp_leaf_spot_{k}.jpg": {"group": f"fam{k // 2}",
                                                   "boxes": [[0.5, 0.5, 0.3, 0.4]]}
             for k in range(30)}
    index["leaf_spot/supp_leaf_spot_moved.jpg"] = {"group": "gone", "boxes": [[0.5, 0.5, 0.2, 0.2]]}
    ns["SUPPLEMENT_BOXES_INDEX"].parent.mkdir(parents=True, exist_ok=True)
    ns["SUPPLEMENT_BOXES_INDEX"].write_text(json.dumps(index))

    ns["build_yolo_detection_dataset_from_plantdoc"]()
    out = ns["DETECTOR_DATA_ROOT"]
    placed = {p.name: p.parent.name for p in (out / "images").rglob("supp_*.jpg")}
    assert len(placed) == 30 and "supp_leaf_spot_moved.jpg" not in placed
    for k in range(0, 30, 2):                      # a family never straddles train/val
        assert placed[f"supp_leaf_spot_{k}.jpg"] == placed[f"supp_leaf_spot_{k + 1}.jpg"]
    some = next((out / "images").rglob("supp_leaf_spot_0.jpg"))
    assert os.path.samefile(some, train / "leaf_spot" / "supp_leaf_spot_0.jpg"), "not hard-linked"
    label = (out / "labels" / some.parent.name / "supp_leaf_spot_0.txt").read_text()
    assert label == "0 0.500000 0.500000 0.300000 0.400000\n"


# ── §2 tail: the re-stamped next manifest ─────────────────────────────────


def _restamp(ns: dict, tmp_path: Path) -> list[dict]:
    s2 = _cell("def fetch_kaggle_supplement(")
    block = s2[s2.index("# ── The next supplement manifest, re-stamped"):]
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        exec(compile(block, "<notebook §2 next manifest>", "exec"), ns)  # noqa: S102
    finally:
        os.chdir(cwd)
    with (tmp_path / "models" / "supplement_manifest_next.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_next_manifest_stamps_exactly_the_rows_this_run_trained(tmp_path):
    ns = _base_ns(tmp_path)
    rows = _v8_rows(n_eval=4)
    rows.append({"filename": "leaf_spot/old.jpg", "class": "leaf_spot", "hn_kind": "",
                 "split_hint": "train_only", "confidence": "0.9",
                 "first_seen_model_version": "2.6.8"})
    supp = _supplement(tmp_path, rows, extra_files=["leaf_spot/new_scan.jpg"])
    _run(ns, supp)
    # Stand-in for the splits: one eval row was carved into a held-out set.
    held = ns["TRAIN_ROOT"] / "healthy" / "supp_healthy_img_000.jpg"
    held.rename(tmp_path / "held_out.jpg")
    out = {r["filename"]: r for r in _restamp(ns, tmp_path)}

    assert out["healthy/img_000.jpg"]["first_seen_model_version"] == ""       # held out
    assert out["leaf_spot/img_001.jpg"]["first_seen_model_version"] == "2.7.5"
    assert out["not_a_plant/bnd_000.jpg"]["first_seen_model_version"] == "2.7.5"
    assert out["leaf_spot/old.jpg"]["first_seen_model_version"] == "2.6.8"    # kept
    new = out["leaf_spot/new_scan.jpg"]                                       # adopted
    assert new["split_hint"] == "train_only" and new["first_seen_model_version"] == "2.7.5"
    # Every original column survives, values unchanged apart from the stamp.
    assert out["leaf_spot/img_001.jpg"]["confidence"] == "0.95"
    assert out["leaf_spot/img_001.jpg"]["split_hint"] == "eval_ok"
