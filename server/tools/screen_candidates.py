#!/usr/bin/env python3
"""Pre-screen collection candidates before review and publishing.

The v9 minimum round (``docs/V9_MINIMUM_ROUND.md``) needs early-stage scorch
and houseplant ``leaf_spot`` photos, reviewed by an agent before they enter
the supplement. This tool does the mechanical part first, so that review only
spends time where a judgement is needed:

* **Drops only objective defects** (verdict ``drop:<reason>``): an unreadable
  file, a short side under ``COLLECTION_MIN_SIDE``, a frame under the server's
  blur floor, a near-duplicate inside the batch (the first copy is kept), a
  near-duplicate of a reference corpus (``--against``, e.g. the published
  supplement) and a near-duplicate of the OOD benchmark (``--bench``, exit 2).
  Nothing is dropped for how it looks: a photo that only *seems* off goes to
  ``review``.
* **Sorts the rest into buckets** from two colour measures taken inside the
  photo's YOLO boxes (or the whole frame without boxes): ``brown_frac``, the
  share of leaf pixels that read tan/brown, and ``edge_share``, the share of
  that brown lying in patches that touch the leaf's outline. Marginal scorch
  is brown at the edge of a still-green blade; ``leaf_spot`` is brown inside
  it. The buckets order the review queue; they never admit or reject a row.
* **Checks the round's rules**: boxes present where the stratum needs them,
  at most ``SYNTHETIC_MAX_SHARE`` AI-generated photos among the kept ones
  (exit 3 otherwise), and families still missing against the targets.

Outputs ``<out>.csv`` (one row per photo, manifest-ready columns first),
``<out>.json`` (counts) and, with ``--sheets``, contact sheets per class and
bucket for the reviewer.

Standalone on purpose: it runs from the task archive without the server code.
The gates it shares with the server are copies, and
``tests/test_screen_candidates.py`` fails if they drift.

Usage::

    python screen_candidates.py --root batch/ --out report/batch \\
        --against supplement_v8/ --bench oodbench/ --sheets report/sheets
    # v8 healthy boundary negatives that may be early scorch:
    python screen_candidates.py --root supplement_v8/ --meta supplement_v8/manifest.csv \\
        --where hn_kind=boundary --only-classes healthy --out report/v8_relabel --sheets report/relabel
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

# ── Gates shared with the server (pinned by tests/test_screen_candidates.py) ──
COLLECTION_MIN_SIDE = 384      # tools/filter_supplement.COLLECTION_MIN_SIDE
BLUR_LAPLACIAN_FLOOR = 50.0    # pipeline.BLUR_LAPLACIAN_FLOOR
BLUR_DOWNSCALE = 320           # pipeline.BLUR_DOWNSCALE
BLUR_EXEMPT = frozenset({"not_a_plant"})
PHASH_MAX_HAMMING = 6          # docs/DATASET_LABELING_PLAN.md "Image gates"
SIFT_RATIO_TEST = 0.75         # tools/build_ood_benchmark.SIFT_*
SIFT_RANSAC_REPROJ_THRESHOLD = 5.0
SIFT_MIN_HOMOGRAPHY_INLIERS = 30
# Drop only at this many inliers; 30-79 is flagged, not dropped. On the first v9
# batch every real copy or same-session sibling of a v8 photo scored 96 or more,
# and one unrelated photo (blinds next to a calathea) scored 47.
SIFT_DROP_INLIERS = 80

HEAD_CLASSES = (
    "gray_mold_botrytis", "healthy", "leaf_scorch_underwatering", "leaf_spot",
    "not_a_plant", "nutrient_deficiency", "pests", "powdery_mildew", "viral_disease",
)
IMG_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
BOXES_REQUIRED = frozenset({"leaf_scorch_underwatering", "leaf_spot", "pests", "gray_mold_botrytis"})
SYNTHETIC_MAX_SHARE = 0.05
# Families still needed in the v9 minimum round (docs/V9_MINIMUM_ROUND.md).
DEFAULT_TARGETS = {"leaf_scorch_underwatering": 187, "leaf_spot": 100}

# ── Colour measures (screening only; nothing is admitted or rejected on them) ──
ANALYSIS_SIDE = 384
BROWN_BAND = 0.08             # brown counts within this share of the short side of green
NO_DAMAGE_BELOW = 0.003        # brown_frac under this: no visible damage
EARLY_MAX_BROWN = 0.08         # scorch at or under this, mostly at the edge: early
LATE_MIN_BROWN = 0.25          # scorch at or over this: end-stage
EDGE_MIN_SHARE = 0.6           # brown mostly in patches touching the outline
SPOT_MAX_EDGE_SHARE = 0.5      # brown mostly inside the blade
HEALTHY_SCORCH_MAX_BROWN = 0.15


def variance_of_laplacian(img: Image.Image, max_side: int = BLUR_DOWNSCALE) -> float:
    """Copy of ``pipeline._variance_of_laplacian`` (same downscale, kernel, ring)."""
    w, h = img.size
    if w <= 0 or h <= 0:
        return 0.0
    scale = max_side / float(max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return 0.0
    lap = (-4.0 * gray + np.roll(gray, -1, axis=0) + np.roll(gray, 1, axis=0)
           + np.roll(gray, -1, axis=1) + np.roll(gray, 1, axis=1))
    inner = lap[1:-1, 1:-1]
    return float(inner.var()) if inner.size else 0.0


def parse_yolo(text: str) -> tuple[list[tuple[float, float, float, float]], bool]:
    """Boxes ``(cx, cy, w, h)`` from a YOLO label file, and whether every row was valid.

    Polygon rows are reduced to their bounding box and a trailing confidence is
    ignored, as notebook §2 does."""
    boxes: list[tuple[float, float, float, float]] = []
    ok = True
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            values = [float(p) for p in parts[1:]]
        except ValueError:
            ok = False
            continue
        if len(values) in (4, 5):
            cx, cy, bw, bh = values[:4]
        elif len(values) >= 6 and len(values) % 2 == 0:
            xs, ys = values[0::2], values[1::2]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        else:
            ok = False
            continue
        if not (0 <= cx - bw / 2 + 1e-6 and cx + bw / 2 <= 1 + 1e-6
                and 0 <= cy - bh / 2 + 1e-6 and cy + bh / 2 <= 1 + 1e-6 and bw > 0 and bh > 0):
            ok = False
            continue
        boxes.append((cx, cy, bw, bh))
    return boxes, ok


def colour_measures(img: Image.Image, boxes: list[tuple[float, float, float, float]]) -> dict[str, Any]:
    """``brown_frac``, ``edge_share``, ``n_lesions``, ``yellow_frac``, ``leaf_frac``, ``sat_mean``.

    Leaf pixels are green ones plus brown ones within a band of green tissue
    (``BROWN_BAND`` of the short side): necrosis borders living tissue, while a
    pot, a hand or a floor only contributes the strip where it meets a leaf.
    Measured on the first v9 batch against manual verdicts, the band halved the
    early-scorch photos sent to ``late`` (4 -> 2 of 19) next to counting whole
    brown patches; no colour rule reached more than ~2 in 3 right in
    ``early_fast``, which is why buckets order the review and decide nothing.
    A brown patch is "edge" when it reaches the outline of the leaf area."""
    import cv2

    rgb = img.convert("RGB")
    w, h = rgb.size
    scale = min(1.0, ANALYSIS_SIDE / float(max(w, h)))
    if scale < 1.0:
        rgb = rgb.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    hsv = np.asarray(rgb.convert("HSV"), dtype=np.float32) / 255.0
    hue, sat, val = hsv[..., 0] * 360.0, hsv[..., 1], hsv[..., 2]
    hh, ww = hue.shape
    region = np.zeros((hh, ww), dtype=bool)
    if boxes:
        for cx, cy, bw, bh in boxes:
            x0, x1 = int((cx - bw / 2) * ww), int(np.ceil((cx + bw / 2) * ww))
            y0, y1 = int((cy - bh / 2) * hh), int(np.ceil((cy + bh / 2) * hh))
            region[max(0, y0):min(hh, y1), max(0, x0):min(ww, x1)] = True
    else:
        region[:] = True
    green = region & (hue >= 35) & (hue <= 170) & (sat >= 0.18) & (val >= 0.15)
    yellow = green & (hue < 60) & (sat >= 0.25) & (val >= 0.3)
    brown = region & ((hue < 35) | (hue >= 345)) & (sat >= 0.20) & (val >= 0.10) & (val <= 0.88)
    out: dict[str, Any] = {
        "sat_mean": round(float(sat[region].mean()) if region.any() else 0.0, 4),
        "leaf_frac": 0.0, "brown_frac": 0.0, "edge_share": 0.0, "n_lesions": 0, "yellow_frac": 0.0,
    }
    if green.sum() < 0.005 * region.sum():
        out["leaf_frac"] = round(float(green.sum()) / max(1, int(region.sum())), 4)
        return out
    k = max(3, int(BROWN_BAND * min(hh, ww)) | 1)
    kept = brown & (cv2.dilate(green.astype(np.uint8), np.ones((k, k), np.uint8)) > 0)
    leaf = green | kept
    rk = max(3, int(0.03 * min(hh, ww)) | 1)
    rim = leaf & ~(cv2.erode(leaf.astype(np.uint8), np.ones((rk, rk), np.uint8)) > 0)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(kept.astype(np.uint8), connectivity=8)
    on_rim = set(np.unique(labels[kept & rim]).tolist())
    brown_px = int(kept.sum())
    edge_px = sum(int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n) if i in on_rim)
    out.update({
        "leaf_frac": round(float(leaf.sum()) / max(1, int(region.sum())), 4),
        "brown_frac": round(brown_px / max(1, int(leaf.sum())), 4),
        "edge_share": round(edge_px / brown_px, 4) if brown_px else 0.0,
        "n_lesions": sum(1 for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 4),
        "yellow_frac": round(float(yellow.sum()) / max(1, int(leaf.sum())), 4),
    })
    return out


def bucket_for(cls: str, m: dict[str, Any]) -> str:
    """Review bucket: an ordering hint for the reviewer, never a verdict."""
    bf, edge = m["brown_frac"], m["edge_share"]
    if m["leaf_frac"] == 0.0 or (bf == 0.0 and m["n_lesions"] == 0 and m["leaf_frac"] < 0.005):
        return "no_leaf_found"
    if cls == "leaf_scorch_underwatering":
        if bf < NO_DAMAGE_BELOW:
            return "no_visible_damage"
        if bf <= EARLY_MAX_BROWN and edge >= EDGE_MIN_SHARE:
            return "early_fast"
        if bf >= LATE_MIN_BROWN:
            return "late"
        return "review"
    if cls == "leaf_spot":
        if bf < NO_DAMAGE_BELOW:
            return "no_visible_damage"
        if edge < SPOT_MAX_EDGE_SHARE and m["n_lesions"] >= 2:
            return "spots_fast"
        if edge >= EDGE_MIN_SHARE:
            return "edge_damage_check"
        return "review"
    if cls == "healthy":
        if bf < NO_DAMAGE_BELOW:
            return "clean"
        if bf <= HEALTHY_SCORCH_MAX_BROWN and edge >= EDGE_MIN_SHARE:
            return "scorch_candidate"
        return "review"
    return "review"


STRATUM_HINT = {"early_fast": "scorch, early/dull", "spots_fast": "leaf_spot, houseplant"}


def _phash_variants(img: Image.Image) -> list[int]:
    """pHash of the image and its 7 rotations/mirrors, as ints (identity first)."""
    import imagehash

    variants = [img, img.rotate(90, expand=True), img.rotate(180), img.rotate(270, expand=True)]
    variants += [ImageOps.mirror(v) for v in variants]
    return [int(str(imagehash.phash(v)), 16) for v in variants]


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _load(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _reference_hashes(roots: list[Path], workers: int) -> list[tuple[str, int]]:
    paths = [p for root in roots for p in sorted(root.rglob("*")) if p.suffix.lower() in IMG_EXTS]

    def one(p: Path) -> tuple[str, int] | None:
        try:
            return str(p), _phash_variants(_load(p))[0]
        except Exception:  # noqa: BLE001 - an unreadable reference file just isn't compared
            return None

    with ThreadPoolExecutor(workers) as ex:
        return [r for r in ex.map(one, paths) if r is not None]


def _sift_matches(cands: list[Path], refs: list[Path], workers: int) -> dict[str, tuple[str, int]]:
    """Best reference match per candidate by SIFT votes + RANSAC inliers (crops, re-encodes).

    pHash misses crops and letterboxed copies: in the first v9 batch it found 9
    of 16 copies already in v8; this pass found the other 7."""
    import cv2

    sift = cv2.SIFT_create(nfeatures=600)

    def feats(p: Path) -> tuple[np.ndarray, np.ndarray | None]:
        try:
            img = _load(p)
            img.thumbnail((640, 640))
            kp, des = sift.detectAndCompute(cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY), None)
            return np.float32([k.pt for k in kp]) if kp else np.zeros((0, 2), np.float32), des
        except Exception:  # noqa: BLE001
            return np.zeros((0, 2), np.float32), None

    with ThreadPoolExecutor(workers) as ex:
        rf = list(ex.map(feats, refs))
        cf = list(ex.map(feats, cands))
    owners = [i for i, (_, d) in enumerate(rf) if d is not None and len(d)]
    if not owners:
        return {}
    descs = np.concatenate([rf[i][1] for i in owners]).astype(np.float32)  # type: ignore[misc]
    owner_of = np.concatenate([np.full(len(rf[i][1]), i) for i in owners])  # type: ignore[arg-type]
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 64})
    flann.add([descs])
    flann.train()
    bf = cv2.BFMatcher(cv2.NORM_L2)
    out: dict[str, tuple[str, int]] = {}
    for path, (pts, des) in zip(cands, cf, strict=True):
        if des is None or len(des) < 4:
            continue
        votes: Counter[int] = Counter()
        for pair in flann.knnMatch(des.astype(np.float32), k=2):
            if len(pair) == 2 and pair[0].distance < SIFT_RATIO_TEST * pair[1].distance:
                votes[int(owner_of[pair[0].trainIdx])] += 1
        best = (None, 0)
        for j, _ in votes.most_common(5):
            rpts, rdes = rf[j]
            good = [a for pair in bf.knnMatch(des, rdes, k=2) if len(pair) == 2
                    for a, b in [pair] if a.distance < SIFT_RATIO_TEST * b.distance]
            if len(good) < 4:
                continue
            _, mask = cv2.findHomography(
                pts[[g.queryIdx for g in good]].reshape(-1, 1, 2),
                rpts[[g.trainIdx for g in good]].reshape(-1, 1, 2),
                cv2.RANSAC, SIFT_RANSAC_REPROJ_THRESHOLD)
            inliers = 0 if mask is None else int(mask.sum())
            if inliers > best[1]:
                best = (str(refs[j]), inliers)
        if best[0] is not None and best[1] >= SIFT_MIN_HOMOGRAPHY_INLIERS:
            out[str(path)] = (best[0], best[1])
    return out


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def _read_meta(meta: Path | None, where: list[str]) -> dict[str, dict[str, str]] | None:
    if meta is None:
        return None
    with meta.open(newline="", encoding="utf-8-sig") as fh:  # tolerate a byte-order mark
        rows = list(csv.DictReader(fh))
    filters = [w.split("=", 1) for w in where]
    keep = {}
    for r in rows:
        if all((r.get(k) or "").strip() == v for k, v in filters):
            keep[(r.get("filename") or "").strip().replace("\\", "/")] = r
    return keep


def screen(root: Path, *, meta: dict[str, dict[str, str]] | None = None, meta_filters: bool = False,
           only_classes: set[str] | None = None, against: list[Path] | None = None,
           bench: Path | None = None, sift: bool = False, workers: int = 8) -> list[dict[str, Any]]:
    """Screen every photo under ``root/<class>/``; returns one dict per photo.

    ``meta`` adds per-photo columns (a photo without a meta row is still
    screened); with ``meta_filters`` only photos whose meta row passed the
    ``--where`` filters are screened."""
    paths = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXTS
                   and p.relative_to(root).parts[0] in HEAD_CLASSES)
    if only_classes:
        paths = [p for p in paths if p.relative_to(root).parts[0] in only_classes]
    if meta is not None and meta_filters:
        paths = [p for p in paths if p.relative_to(root).as_posix() in meta]

    def probe(p: Path) -> dict[str, Any]:
        rel = p.relative_to(root).as_posix()
        cls = rel.split("/", 1)[0]
        row: dict[str, Any] = {"filename": rel, "class": cls, "verdict": "keep", "flags": []}
        label = p.with_suffix(".txt")
        boxes: list[tuple[float, float, float, float]] = []
        if label.exists():
            boxes, ok = parse_yolo(label.read_text(encoding="utf-8", errors="replace"))
            if not ok:
                row["flags"].append("bad_box_rows")
        row["n_boxes"] = len(boxes)
        try:
            img = _load(p)
        except Exception:  # noqa: BLE001 - unreadable is a verdict, not a crash
            row["verdict"] = "drop:corrupt"
            return row
        short = min(img.size)
        blur = variance_of_laplacian(img)
        row.update({"short_side": short, "blur": round(blur, 1)})
        row["_hashes"] = _phash_variants(img)
        row["phash"] = f"{row['_hashes'][0]:016x}"
        if short < COLLECTION_MIN_SIDE:
            row["verdict"] = "drop:too_small"
        elif cls not in BLUR_EXEMPT and blur < BLUR_LAPLACIAN_FLOOR:
            row["verdict"] = "drop:blurry"
        row.update(colour_measures(img, boxes))
        row["bucket"] = bucket_for(cls, row)
        if cls in BOXES_REQUIRED and not boxes:
            row["flags"].append("needs_boxes")
        return row

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(probe, paths))

    # Inside the batch: keep the first copy of any near-duplicate cluster. A copy
    # filed under another class is a label contradiction: it is dropped as such
    # and the kept copy is flagged, so the reviewer settles the label.
    kept_hashes: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if "_hashes" not in row:
            continue
        hit = next((first for h, first in kept_hashes
                    if min(_hamming(v, h) for v in row["_hashes"]) <= PHASH_MAX_HAMMING), None)
        if hit is not None and row["verdict"] == "keep":
            same = hit["class"] == row["class"]
            row["verdict"] = "drop:dup_in_batch" if same else "drop:dup_other_class"
            row["dup_of"] = hit["filename"]
            if not same and "dup_in_other_class" not in hit["flags"]:
                hit["flags"].append("dup_in_other_class")
        else:
            kept_hashes.append((row["_hashes"][0], row))

    for kind, roots in (("bench_leak", [bench] if bench else []), ("dup_of_reference", against or [])):
        if not roots:
            continue
        refs = _reference_hashes(roots, workers)
        for row in rows:
            if "_hashes" not in row or not row["verdict"].startswith("keep"):
                continue
            hit = next((name for name, h in refs
                        if min(_hamming(v, h) for v in row["_hashes"]) <= PHASH_MAX_HAMMING), None)
            if hit:
                row["verdict"], row["dup_of"] = f"drop:{kind}", hit
    if sift and against:
        refs_paths = [p for r in against for p in sorted(r.rglob("*")) if p.suffix.lower() in IMG_EXTS]
        cand = [root / row["filename"] for row in rows if row["verdict"] == "keep"]
        for path, (ref, inliers) in _sift_matches(cand, refs_paths, workers).items():
            row = next(r for r in rows if root / r["filename"] == Path(path))
            row["dup_of"] = f"{ref} (sift {inliers})"
            if inliers >= SIFT_DROP_INLIERS:
                row["verdict"] = "drop:dup_of_reference"
            else:  # a weaker geometric match can be repetitive texture: the reviewer decides
                row["flags"].append("possible_copy_of_reference")

    for row in rows:
        m = (meta or {}).get(row["filename"], {})
        row["synthetic"] = int(_truthy(m.get("synthetic")) or Path(row["filename"]).name.startswith("syn_"))
        row["capture_family_id"] = (m.get("capture_family_id") or "").strip()
        row["stratum_candidate"] = STRATUM_HINT.get(row.get("bucket", ""), "")
        # Synthetic photos never grade a model; everything else may, after review.
        row["split_hint"] = "train_only" if row["synthetic"] else "eval_ok"
        row["first_seen_model_version"] = "never"
        row.pop("_hashes", None)
        row["flags"] = ";".join(row["flags"])
        for key, value in m.items():
            row.setdefault(f"meta_{key}", value)
    return rows


def summarise(rows: list[dict[str, Any]], targets: dict[str, int]) -> dict[str, Any]:
    kept = [r for r in rows if r["verdict"] == "keep"]
    synthetic = sum(r["synthetic"] for r in kept)
    families: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in kept:
        families[r["class"]][r.get("bucket", "")].add(r["capture_family_id"] or r["filename"])
    progress = {}
    for cls, needed in targets.items():
        fast = "early_fast" if cls == "leaf_scorch_underwatering" else "spots_fast"
        fam = families.get(cls, {})
        all_fams = set().union(*fam.values()) if fam else set()
        progress[cls] = {
            "families_still_needed_before_batch": needed,
            "kept_families": len(all_fams),
            "fast_track_families": len(fam.get(fast, set())),
            "left_if_all_kept_pass_review": max(0, needed - len(all_fams)),
        }
    return {
        "photos": len(rows),
        "kept": len(kept),
        "verdicts": dict(Counter(r["verdict"] for r in rows)),
        "buckets_kept": {c: dict(Counter(r.get("bucket", "") for r in kept if r["class"] == c))
                         for c in sorted({r["class"] for r in kept})},
        "flags_kept": dict(Counter(f for r in kept for f in r["flags"].split(";") if f)),
        "synthetic_kept": synthetic,
        "synthetic_share": round(synthetic / len(kept), 4) if kept else 0.0,
        "synthetic_max_share": SYNTHETIC_MAX_SHARE,
        "targets": progress,
    }


MANIFEST_FIRST = ["filename", "class", "split_hint", "first_seen_model_version", "capture_family_id",
                  "stratum_candidate", "synthetic", "verdict", "dup_of", "bucket", "flags", "brown_frac",
                  "edge_share", "n_lesions", "yellow_frac", "leaf_frac", "sat_mean", "n_boxes", "short_side", "blur", "phash"]


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    extra = sorted({k for r in rows for k in r} - set(MANIFEST_FIRST))
    with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:  # no BOM
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIRST + extra, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def contact_sheets(root: Path, rows: list[dict[str, Any]], out_dir: Path, per_sheet: int = 12) -> int:
    """JPEG grids per class/bucket (boxes drawn, measures labelled) for the reviewer."""
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = f"{r['class']}__{r.get('bucket') or 'none'}" if r["verdict"] == "keep" else f"{r['class']}__dropped"
        groups[key].append(r)
    tile, cols, written = 360, 4, 0
    for key, items in sorted(groups.items()):
        for start in range(0, len(items), per_sheet):
            chunk = items[start:start + per_sheet]
            sheet_rows = (len(chunk) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * tile, sheet_rows * (tile + 34)), "white")
            draw = ImageDraw.Draw(sheet)
            for i, r in enumerate(chunk):
                x, y = (i % cols) * tile, (i // cols) * (tile + 34)
                try:
                    img = _load(root / r["filename"])
                except Exception:  # noqa: BLE001
                    continue
                img.thumbnail((tile, tile))
                label = (root / r["filename"]).with_suffix(".txt")
                if label.exists():
                    d = ImageDraw.Draw(img)
                    for cx, cy, bw, bh in parse_yolo(label.read_text(encoding="utf-8", errors="replace"))[0]:
                        iw, ih = img.size
                        d.rectangle([(cx - bw / 2) * iw, (cy - bh / 2) * ih, (cx + bw / 2) * iw,
                                     (cy + bh / 2) * ih], outline=(255, 0, 255), width=3)
                sheet.paste(img, (x, y + 34))
                name = Path(r["filename"]).name
                draw.text((x + 4, y + 2), name[:44], fill="black")
                draw.text((x + 4, y + 16), f"{r['verdict']} bf={r.get('brown_frac', '')} "
                                           f"edge={r.get('edge_share', '')} {r['flags']}"[:60], fill="black")
            sheet.save(out_dir / f"{key}_{start // per_sheet:03d}.jpg", quality=85)
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True, help="folder holding <class>/<photo> (+ <photo>.txt boxes)")
    ap.add_argument("--out", type=Path, required=True, help="output prefix (.csv + .json)")
    ap.add_argument("--meta", type=Path, help="CSV keyed by filename (<class>/<file>): synthetic, capture_family_id, ...")
    ap.add_argument("--where", action="append", default=[], help="keep only meta rows with COL=VALUE (repeatable)")
    ap.add_argument("--only-classes", help="comma-separated class folders to screen")
    ap.add_argument("--against", type=Path, action="append", default=[],
                    help="reference corpus to drop near-duplicates of (repeatable), e.g. the published supplement")
    ap.add_argument("--bench", type=Path, help="OOD benchmark folder; a near-duplicate of it exits 2")
    ap.add_argument("--sift", action="store_true", help="also match crops/re-encodes against --against (slow)")
    ap.add_argument("--sheets", type=Path, help="write contact sheets per class and bucket here")
    ap.add_argument("--targets", default=",".join(f"{k}={v}" for k, v in DEFAULT_TARGETS.items()),
                    help="families still needed, CLASS=N,...")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    targets = {k: int(v) for k, v in (t.split("=", 1) for t in args.targets.split(",") if t)}
    rows = screen(
        args.root, meta=_read_meta(args.meta, args.where), meta_filters=bool(args.where),
        only_classes=set(args.only_classes.split(",")) if args.only_classes else None,
        against=args.against, bench=args.bench, sift=args.sift, workers=args.workers,
    )
    summary = summarise(rows, targets)
    write_outputs(rows, summary, args.out)
    if args.sheets:
        summary["contact_sheets"] = contact_sheets(args.root, rows, args.sheets)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["verdicts"].get("drop:bench_leak"):
        print("STOP: photos duplicate the OOD benchmark; they must not enter the supplement", file=sys.stderr)
        return 2
    if summary["synthetic_share"] > SYNTHETIC_MAX_SHARE:
        print(f"STOP: {summary['synthetic_share']:.1%} of kept photos are AI-generated "
              f"(cap {SYNTHETIC_MAX_SHARE:.0%})", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
