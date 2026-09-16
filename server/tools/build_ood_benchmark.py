#!/usr/bin/env python3
"""Build a family-deduplicated unseen-domain diagnostic for ``paired_eval``.

This is not a general OOD-detection benchmark: it contains healthy ornamental
leaves and one labelled disease arm, but no non-plant/OOD class.  The historical
filename is retained for compatibility; reports must describe it as an
``unseen-domain regression benchmark``.

Why this exists
---------------
The first version (2026-08-11) surfaced a possible regression, then failed the
near-duplicate criterion required of any evaluation set: 2,000 files held only
501 SHA-unique images and 206 pHash clusters at Hamming <= 6. Its counts and
p-values were therefore pseudo-replicated, and one apparent finding vanished
after deduplication. It cannot establish a historical release conclusion.

So this script does the gating in code rather than in a follow-up audit:

* **pHash plus geometric-family dedup**, keeping ONE representative per
  single-link cluster.  pHash catches ordinary edits but misses rotations and
  noisy re-encodes; SIFT correspondences followed by a RANSAC homography catch
  those pseudo-replicates.  SHA dedup alone is not enough.
* **Species is recorded and reported**, because a one-species/source result is
  not a class-general result. A per-class number that is really a per-species
  number is a trap.
* **A minimum independent-family count per class**, so a diagnostic cannot
  silently claim an image count that is mostly augmented copies.

The existing private v2 artifact is a *diagnostic sentinel*, not a release
gate: its 31 scorch rows come from one studio domain and contain confirmed
rotated/noised pseudo-replicates.  Rebuild it with this tool before quoting an
effective sample size, and do not turn it into a release gate until a genuinely
independent, deployment-representative v3 exists.

The source must be in NO training corpus. The default
(`shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`, MIT) is
deliberately excluded from `KAGGLE_ORNAMENTAL_SLUGS` — see
`DATASET_LABELING_PLAN.md`, which rejects it as *training* data precisely
because its labels are unverified. That is fine here and not a contradiction:
label noise in a PAIRED comparison hits both models equally, and the models
disagreeing 32-0 on identical labels cannot be explained by the labels.

Usage
-----
::

    # From an already-extracted copy:
    python -m server.tools.build_ood_benchmark --src ext/flower_data --out oodbench

    # For a source above the exhaustive safety cap, first run the same
    # SIFT+RANSAC family audit offline/blocked in batches and provide an
    # exhaustive CSV with source_relative_path,capture_family_id,family_method
    # (family_method must be sift_ransac_v1):
    python -m server.tools.build_ood_benchmark --src ext/flower_data --out oodbench \
        --family-manifest audited_families.csv

    # Or download it first (needs KAGGLE_API_TOKEN):
    curl -sSL -H "Authorization: Bearer $KAGGLE_API_TOKEN" \\
      "https://www.kaggle.com/api/v1/datasets/download/shuvokumarbasak4004/\\
    flower-leaf-diseases-dataset-new-and-update" -o ext.zip && unzip -q ext.zip -d ext

Then score it end-to-end (NOT Stage-2 argmax — the OOD AND-gate alone can move
ornamental recall ~20pp)::

    python -m server.tools.paired_eval --bundle-a <incumbent> --bundle-b <candidate> \\
        --eval-dir oodbench --provenance '' --out preds.json --run-bootstrap
    python -m server.tools.build_ood_benchmark --report preds.json --manifest oodbench/manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path, PurePosixPath

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Source condition folder -> our taxonomy. Deliberately small: only conditions
# that map UNAMBIGUOUSLY. 'yld' (yellow leaf disease), 'death_leaf', 'blight',
# 'early_blight', 'red_spot' and 'insect_bite' are dropped — guessing a mapping
# would put label noise into the one eval set whose whole job is to be trusted.
CONDITION_MAP = {
    "healthy": "healthy",
    "scorch": "leaf_scorch_underwatering",
}
PHASH_MAX_HAMMING = 6      # DATASET_LABELING_PLAN.md "Image gates"
SIFT_MIN_HOMOGRAPHY_INLIERS = 30
SIFT_RATIO_TEST = 0.75
SIFT_RANSAC_REPROJ_THRESHOLD = 5.0
MAX_EXHAUSTIVE_SIFT_IMAGES = 500
MAX_EXHAUSTIVE_CROSS_CLASS_SIFT_PAIRS = 125_000
PRECOMPUTED_FAMILY_METHOD = "sift_ransac_v1"
MIN_CLUSTERS_PER_CLASS = 60
SOURCE_DATASET_SLUG = "shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update"
SOURCE_DATASET_VERSION = "1"
SOURCE_LICENSE_ID = "MIT"
SOURCE_URL = f"https://www.kaggle.com/datasets/{SOURCE_DATASET_SLUG}/versions/{SOURCE_DATASET_VERSION}"
SOURCE_RIGHTS_STATUS = "dataset_page_declaration_only; per-image_rights_unverified"

# ── Taxonomy arms ────────────────────────────────────────────────────
# Do not pool different morphologies under a shared folder word. Manual review
# found the source's banana_bush "scorch" branch looks primarily like green
# turgor loss, whereas the labelled hibiscus branch shows necrotic tissue.
# Neither interpretation is medically adjudicated here, so the tool keeps only
# an explicit diagnostic arm and never turns folder-name performance into a
# release verdict.
#
# ARM ASSIGNMENT IS AN EXPLICIT LIST, NOT A MEASUREMENT — and that is a
# retreat, recorded honestly. The intent was to sort species automatically by
# the brown fraction of their subject pixels, so a new species would classify
# itself. Implemented (`brown_fraction` below) it does not survive contact with
# the data: it scores *healthy* night_flowering_jasmine at 0.74 and puts
# banana_bush scorch (0.45) ABOVE hibiscus scorch (0.28) — the reverse of the
# ordering the arms exist to capture. The `V < 0.75` term catches shadow and
# dark green, so on these photographs it is closer to a darkness detector than
# a necrosis detector. The column is still written because it is cheap and a
# better rule can be checked against it, but NOTHING branches on it.
#
# So the arms are declared. Adding a species means looking at its images and
# deciding which condition they show; the tool warns rather than guessing.
SPECIES_ARMS = {
    "hibiscus": "necrosis",       # dead brown tissue — what our class is trained on
}
DEFAULT_ARM = "unassigned"

# Arms whose diseased images are not reliably aligned to this project's class
# definition are dropped instead of being relabelled by assumption.
#
# Only the DISEASED images are dropped. Healthy banana_bush stays: it is
# correctly labelled, the species is an in-domain garden ornamental, and cutting
# it would shrink the false-fire arm for no gain.
RETIRED_DISEASE_ARMS = {"banana_bush"}

# Two-sided 95% Student-t critical values by degrees of freedom, for the
# between-species interval in ``report()``. Inlined rather than importing scipy:
# this is a dev tool and the runtime image deliberately carries neither scipy
# nor sklearn. Falls back to the normal 1.96 above df=30, where they agree to
# within 2%.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def brown_fraction(path: Path) -> float | None:
    """Fraction of SUBJECT pixels reading brown/dark rather than green.

    DIAGNOSTIC ONLY — see SPECIES_ARMS. Recorded in the manifest so a future,
    better necrosis measure has something to be compared against; it is not
    accurate enough to assign arms and nothing in this file branches on it.

    Subject = non-background. These are detached-leaf-on-a-sheet photographs, so
    the near-white paper is excluded; otherwise a big white border dilutes every
    measurement toward zero.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB").resize((160, 160))
            hsv = np.asarray(rgb.convert("HSV"), dtype=np.float32) / 255.0
    except Exception:
        return None
    # Vectorised over the 160x160 grid. The per-pixel colorsys loop this
    # replaces called Image.getdata(), removed in Pillow 14 (Oct 2027), and ran
    # 25,600 Python-level conversions per image. PIL's HSV is 8-bit quantised
    # where colorsys is float, so the two disagree slightly: measured over 40
    # benchmark images, max |diff| 0.011, mean 0.005. That is immaterial here —
    # this column is DIAGNOSTIC ONLY (see SPECIES_ARMS above), nothing branches
    # on it, and the arm assignment is declared rather than derived. Do not
    # start branching on it without re-checking that bound.
    h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    background = (v > 0.90) & (sat < 0.15)        # paper / blown-out background
    subject = ~background
    green = (h >= 60 / 360) & (h <= 170 / 360)
    brown = subject & ~green & (v < 0.75)
    n = int(subject.sum())
    return (float(brown.sum()) / n) if n else None


def _phash(path: Path):
    import imagehash
    from PIL import Image
    try:
        with Image.open(path) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception:
        return None


def _sift_features(path: Path, cache: dict[Path, tuple[object, object]]):
    """Return cached SIFT keypoints/descriptors, failing closed without OpenCV.

    Rotation/noise families in benchmark v2 are SHA- and pHash-unique.  Silently
    falling back to pHash would therefore re-certify the exact broken artifact
    this builder exists to replace.
    """
    if path in cache:
        return cache[path]
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "robust benchmark-family dedup requires OpenCV with SIFT support "
            "(`pip install opencv-python-headless`); refusing pHash-only build"
        ) from exc
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        cache[path] = ((), None)
        return cache[path]
    keypoints, descriptors = cv2.SIFT_create().detectAndCompute(image, None)
    cache[path] = (keypoints or (), descriptors)
    return cache[path]


def _sift_homography_inliers(
    left: Path,
    right: Path,
    cache: dict[Path, tuple[object, object]] | None = None,
) -> int:
    """Count geometrically consistent SIFT matches between two captures."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "robust benchmark-family dedup requires OpenCV with SIFT support "
            "(`pip install opencv-python-headless`)"
        ) from exc
    cache = {} if cache is None else cache
    kp_left, desc_left = _sift_features(left, cache)
    kp_right, desc_right = _sift_features(right, cache)
    if desc_left is None or desc_right is None or len(desc_left) < 4 or len(desc_right) < 4:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(desc_left, desc_right, k=2)
    good = [a for pair in pairs if len(pair) == 2
            for a, b in [pair] if a.distance < SIFT_RATIO_TEST * b.distance]
    if len(good) < 4:
        return 0
    src = np.float32([kp_left[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_right[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _homography, mask = cv2.findHomography(
        src, dst, cv2.RANSAC, SIFT_RANSAC_REPROJ_THRESHOLD)
    return 0 if mask is None else int(mask.ravel().sum())


def cluster_paths(
    paths: list[Path],
    threshold: int = PHASH_MAX_HAMMING,
    robust: bool = True,
    family_ids: dict[Path, str] | None = None,
) -> list[list[Path]]:
    """Single-link capture families using pHash and geometric correspondences."""
    decoded, undecodable = [], []
    for p in sorted(paths):
        h = _phash(p)
        if h is None:
            undecodable.append(p)
        else:
            decoded.append((p, h))
    if undecodable:
        # A corrupt file used to vanish here with no count, and the
        # ``files -> families`` line then attributed the shrink to dedup.
        raise RuntimeError(
            f"{len(undecodable)} image(s) could not be decoded for pHash and would "
            f"silently disappear from the benchmark; fix or remove them first. "
            f"Examples: {[str(p) for p in undecodable[:5]]}")
    if robust and len(decoded) > MAX_EXHAUSTIVE_SIFT_IMAGES and family_ids is None:
        pairs = len(decoded) * (len(decoded) - 1) // 2
        raise RuntimeError(
            f"refusing {pairs:,} exhaustive SIFT/RANSAC comparisons for "
            f"{len(decoded)} images. Supply --family-manifest produced by an "
            f"offline {PRECOMPUTED_FAMILY_METHOD} audit; pHash-only fallback is unsafe")
    if family_ids is not None:
        missing = [p for p, _h in decoded if not family_ids.get(p)]
        if missing:
            raise RuntimeError(
                f"precomputed family manifest misses {len(missing)} decoded rows; "
                f"examples: {missing[:5]}")
    parent = list(range(len(decoded)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for i, (_p, h) in enumerate(decoded):
        for j in range(i):
            if (h - decoded[j][1]) <= threshold:
                union(i, j)

    if family_ids is not None:
        first: dict[str, int] = {}
        for i, (path, _hash) in enumerate(decoded):
            family = family_ids[path]
            if family in first:
                union(i, first[family])
            else:
                first[family] = i

    if robust and family_ids is None:
        feature_cache: dict[Path, tuple[object, object]] = {}
        # pHash-connected rows need no second comparison.  Every other pair is
        # checked because an arbitrary rotation/noisy edit can move far outside
        # the pHash radius while still being the same underlying capture.
        for i, (path_i, _hash_i) in enumerate(decoded):
            for j in range(i):
                if find(i) == find(j):
                    continue
                path_j = decoded[j][0]
                if _sift_homography_inliers(path_i, path_j, feature_cache) \
                        >= SIFT_MIN_HOMOGRAPHY_INLIERS:
                    union(i, j)

    groups: dict[int, list[Path]] = defaultdict(list)
    for i, (path, _hash) in enumerate(decoded):
        groups[find(i)].append(path)
    return [groups[root] for root in sorted(groups)]


def dedup(
    paths: list[Path],
    threshold: int = PHASH_MAX_HAMMING,
    robust: bool = True,
    family_ids: dict[Path, str] | None = None,
) -> list[Path]:
    """Keep one deterministic representative per independent capture family."""
    return [group[0] for group in cluster_paths(
        paths, threshold, robust, family_ids)]


def _load_family_manifest(path: Path, src: Path) -> dict[Path, str]:
    """Load an exhaustive offline SIFT/RANSAC family assignment."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    required = {"source_relative_path", "capture_family_id", "family_method"}
    if not rows or required - set(rows[0]):
        raise RuntimeError(
            f"family manifest must contain {sorted(required)} and at least one row")
    out: dict[Path, str] = {}
    for line, row in enumerate(rows, start=2):
        raw = (row.get("source_relative_path") or "").strip().replace("\\", "/")
        rel = PurePosixPath(raw)
        family = (row.get("capture_family_id") or "").strip()
        method = (row.get("family_method") or "").strip()
        if (not raw or rel.is_absolute() or ".." in rel.parts or not family
                or method != PRECOMPUTED_FAMILY_METHOD):
            raise RuntimeError(
                f"invalid family manifest line {line}: path={raw!r}, "
                f"family={family!r}, method={method!r}")
        local = src.joinpath(*rel.parts).resolve()
        if src != local and src not in local.parents:
            raise RuntimeError(f"family manifest path escapes source at line {line}: {raw!r}")
        if local in out:
            raise RuntimeError(f"duplicate family-manifest path at line {line}: {raw!r}")
        out[local] = family
    return out


def build(src: Path, out: Path, per_class_cap: int,
          min_clusters_per_class: int = MIN_CLUSTERS_PER_CLASS,
          *, robust_dedup: bool = True,
          family_manifest: Path | None = None) -> None:
    """Build a capped, class-wide family-deduped diagnostic benchmark."""
    src, out = src.resolve(), out.resolve()
    if not src.is_dir():
        raise ValueError(f"source directory does not exist: {src}")
    if per_class_cap <= 0 or min_clusters_per_class <= 0:
        raise ValueError("per_class_cap and min_clusters_per_class must be positive")
    if src == out or src in out.parents or out in src.parents:
        raise ValueError("--src and --out must be separate, non-nested directories")
    precomputed_families = (
        _load_family_manifest(family_manifest.resolve(), src)
        if family_manifest is not None else None)

    # group: class -> species -> [paths]
    pool: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    for split_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        for species_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
            for cond_dir in sorted(p for p in species_dir.iterdir() if p.is_dir()):
                cls = CONDITION_MAP.get(cond_dir.name.lower())
                if cls:
                    pool[cls][species_dir.name].extend(
                        f for f in cond_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in IMG_EXTS)
    expected_classes = set(CONDITION_MAP.values())
    missing_source_classes = expected_classes - set(pool)
    if missing_source_classes:
        raise RuntimeError(f"source has no mapped rows for {sorted(missing_source_classes)}")

    # Dedup across the WHOLE class, not once per species. A re-uploaded photo
    # under two species is still one capture and must not count twice.
    unique_by_class: dict[str, dict[str, list[Path]]] = {}
    class_hashes: dict[str, list[tuple[Path, object]]] = {}
    family_size_by_path: dict[Path, int] = {}
    for cls, by_species in sorted(pool.items()):
        candidates = []
        for species, files in sorted(by_species.items()):
            if cls != "healthy" and species in RETIRED_DISEASE_ARMS:
                print(f"  {species:28s} RETIRED for {cls} — {len(files)} files skipped")
                continue
            candidates.extend(files)
        class_family_ids = ({p: precomputed_families.get(p, "") for p in candidates}
                            if precomputed_families is not None else None)
        families = cluster_paths(
            candidates, robust=robust_dedup, family_ids=class_family_ids)
        unique = [family[0] for family in families]
        for family in families:
            family_size_by_path[family[0]] = len(family)
        regrouped: dict[str, list[Path]] = defaultdict(list)
        for p in unique:
            regrouped[p.parent.parent.name].append(p)
        unique_by_class[cls] = dict(regrouped)
        class_hashes[cls] = [(p, h) for p in unique if (h := _phash(p)) is not None]

    # Contradictory labels within either duplicate signal are not "dedup": they
    # are a benchmark-integrity error. Stop instead of keeping whichever class
    # sorts first. Same basenames in different class directories remain fine
    # when the content differs; downstream joins key on class/path.
    conflicts = []
    cross_class_feature_cache: dict[Path, tuple[object, object]] = {}
    classes = sorted(class_hashes)
    cross_pairs = sum(
        len(class_hashes[a]) * len(class_hashes[b])
        for i, a in enumerate(classes) for b in classes[i + 1:])
    if (robust_dedup and precomputed_families is None
            and cross_pairs > MAX_EXHAUSTIVE_CROSS_CLASS_SIFT_PAIRS):
        raise RuntimeError(
            f"refusing {cross_pairs:,} cross-class SIFT/RANSAC comparisons; "
            f"supply --family-manifest ({PRECOMPUTED_FAMILY_METHOD})")
    for i, cls_a in enumerate(classes):
        for cls_b in classes[i + 1:]:
            for pa, ha in class_hashes[cls_a]:
                for pb, hb in class_hashes[cls_b]:
                    distance = int(ha - hb)
                    inliers = 0
                    same_precomputed_family = (
                        precomputed_families is not None
                        and precomputed_families.get(pa) == precomputed_families.get(pb))
                    if (robust_dedup and precomputed_families is None
                            and distance > PHASH_MAX_HAMMING):
                        inliers = _sift_homography_inliers(
                            pa, pb, cross_class_feature_cache)
                    if distance <= PHASH_MAX_HAMMING \
                            or inliers >= SIFT_MIN_HOMOGRAPHY_INLIERS \
                            or same_precomputed_family:
                        conflicts.append((cls_a, pa, cls_b, pb, distance, inliers))
                        if len(conflicts) >= 12:
                            break
                if len(conflicts) >= 12:
                    break
    if conflicts:
        preview = "\n".join(
            f"  {a}:{pa} <-> {b}:{pb} (pHash d={d}, SIFT inliers={n})"
            for a, pa, b, pb, d, n in conflicts)
        raise RuntimeError("cross-class duplicate-family conflicts in benchmark:\n" + preview)

    # Round-robin the class cap across active species. The old floor-division
    # quota counted retired/empty species in its denominator and max(1, ...)
    # could exceed the requested class cap when species > cap.
    selected: dict[str, dict[str, list[Path]]] = {}
    for cls, by_species in sorted(unique_by_class.items()):
        queues = {s: deque(sorted(v)) for s, v in sorted(by_species.items()) if v}
        chosen: dict[str, list[Path]] = defaultdict(list)
        total = 0
        while queues and total < per_class_cap:
            for species in list(queues):
                if total >= per_class_cap:
                    break
                q = queues[species]
                chosen[species].append(q.popleft())
                total += 1
                if not q:
                    del queues[species]
        selected[cls] = dict(chosen)

    counts = {c: sum(len(v) for v in selected.get(c, {}).values())
              for c in expected_classes}
    thin = {c: n for c, n in counts.items() if n < min_clusters_per_class}
    if thin:
        raise RuntimeError(
            f"under {min_clusters_per_class} independent capture families; "
            f"diagnostic sample size is not credible: {thin}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for cls, by_species in sorted(selected.items()):
        print(f"\n{cls}:")
        (out / cls).mkdir(parents=True, exist_ok=True)
        for species, take in sorted(by_species.items()):
            browns = []
            arm = SPECIES_ARMS.get(species, DEFAULT_ARM) if cls != "healthy" else ""
            for f in take:
                split = f.parent.parent.parent.name or "x"
                dst = out / cls / f"{split}__{species}__{f.stem}{f.suffix.lower()}"
                if dst.exists():
                    raise RuntimeError(f"destination collision: {dst}")
                shutil.copy(f, dst)
                bf = brown_fraction(dst)
                if bf is not None:
                    browns.append(bf)
                rows.append({
                    "filename": f"{cls}/{dst.name}", "class": cls,
                    "species": species, "source": SOURCE_DATASET_SLUG,
                    "source_slug": SOURCE_DATASET_SLUG,
                    "source_version": SOURCE_DATASET_VERSION,
                    "source_relative_path": f.relative_to(src).as_posix(),
                    "license_id": SOURCE_LICENSE_ID, "source_url": SOURCE_URL,
                    "rights_status": SOURCE_RIGHTS_STATUS,
                    "release_eligible": "false",
                    "capture_family_id": (
                        precomputed_families[f] if precomputed_families is not None
                        else hashlib.sha256(
                            (SOURCE_DATASET_SLUG + ":" + f.relative_to(src).as_posix()).encode()
                        ).hexdigest()[:16]),
                    "family_method": (
                        PRECOMPUTED_FAMILY_METHOD if precomputed_families is not None
                        else "exhaustive_sift_ransac_v1"),
                    "source_family_size": family_size_by_path[f],
                    "brown_fraction": "" if bf is None else f"{bf:.4f}",
                    "arm": arm,
                })
            med = sorted(browns)[len(browns) // 2] if browns else None
            raw_n = len(pool[cls].get(species, []))
            uniq_n = len(unique_by_class[cls].get(species, []))
            print(f"  {species:28s} {raw_n:5d} files -> {uniq_n:4d} families -> "
                  f"{len(take):4d} kept   brown_median="
                  f"{'n/a' if med is None else f'{med:.3f}'}  arm={arm}")
            if arm == DEFAULT_ARM and cls != "healthy":
                print(f"    WARN: {species!r} is not in SPECIES_ARMS; inspect and assign it")

    keys = [r["filename"] for r in rows]
    dupes = [k for k, n in Counter(keys).items() if n != 1]
    if dupes:
        raise RuntimeError(f"duplicate class/path manifest keys: {dupes[:12]}")
    fields = ["filename", "class", "species", "source", "source_slug",
              "source_version", "source_relative_path", "license_id", "source_url",
              "rights_status", "release_eligible",
              "capture_family_id", "family_method", "source_family_size",
              "brown_fraction", "arm"]
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*")
               if p.is_file() and p.suffix.lower() in IMG_EXTS}
    if set(keys) != on_disk or len(keys) != len(on_disk):
        raise RuntimeError("manifest/image class-path keys disagree after build")
    print(f"\nwrote unseen-domain regression benchmark {out} — {len(rows)} images")
    for cls in sorted(expected_classes):
        n_sp = len(selected.get(cls, {}))
        if n_sp < 3:
            print(f"WARN: {cls} spans only {n_sp} species — report PER SPECIES and do "
                  f"not read a class-level delta as a domain-level one.")


def report(preds_path: Path, manifest: Path) -> None:
    """Per-species paired breakdown of a preds.json produced by paired_eval."""
    import numpy as np

    d = json.loads(preds_path.read_text())
    if "items" not in d:
        sys.exit("preds.json has no `items` — re-run paired_eval (it records image paths now)")
    rows = list(csv.DictReader(open(manifest, newline="", encoding="utf-8")))
    # Key on ``class/name``, NOT the bare basename: the same species+stem exists
    # under two classes (``hibiscus__test1.jpg`` is both a healthy and a scorch
    # file), so a basename key silently overwrites one class with the other and
    # the arm breakdown comes out attached to the wrong rows.
    def _key(p: str) -> str:
        parts = Path(p).parts
        return "/".join(parts[-2:]) if len(parts) >= 2 else str(p)
    species = {_key(r["filename"]): r["species"] for r in rows}
    arms = {_key(r["filename"]): (r.get("arm") or "") for r in rows}
    names = d["class_names"]
    yt = np.array(d["y_true"])
    ya = np.array(d["y_pred_a"])
    yb = np.array(d.get("y_pred_b") or d["y_pred_a"])
    la, lb = d.get("label_a", "a"), d.get("label_b", "b")
    sp = np.array([species.get(_key(i), "?") for i in d["items"]])
    ar = np.array([arms.get(_key(i), "") for i in d["items"]])

    disease_idx = [i for i, n in enumerate(names) if n not in ("healthy", "not_a_plant")]
    healthy_idx = names.index("healthy") if "healthy" in names else None

    def _line(label: str, k, ci: int, note: str = "") -> None:
        ra, rb = float((ya[k] == ci).mean()), float((yb[k] == ci).mean())
        aa, ab = int((ya[k] == -1).sum()), int((yb[k] == -1).sum())
        print(f"{label:38s} {ra:9.3f} {rb:9.3f} {rb - ra:+8.3f} {int(k.sum()):4d}"
              f"   {aa:6d} {ab:6d}{note}")

    def _false_fire_line(label: str, k) -> None:
        fa = float(np.isin(ya[k], disease_idx).mean())
        fb = float(np.isin(yb[k], disease_idx).mean())
        print(f"{label:38s} {fa:9.3f} {fb:9.3f} {fb - fa:+8.3f} {int(k.sum()):4d}")

    # An abstention (-1: blur gate, OOD gate) is scored WRONG for recall and is
    # coverage loss; it is never a false fire. The healthy rows therefore carry
    # two statistics: recall of ``healthy`` (which an abstention lowers) and the
    # FALSE-FIRE rate (a disease call on a healthy image — the user-facing harm,
    # which an abstention does not commit). Reading only the first one made a
    # model that abstains on half its healthy images indistinguishable from one
    # that calls half of them scorched.
    print("recall of the row's own class | n | abstentions (scored wrong, not false fires)")
    print(f"{'class / arm / species':38s} {la:>9s} {lb:>9s} {'delta':>8s}   n   "
          f"{'abst_' + la[:3]:>6s} {'abst_' + lb[:3]:>6s}")
    for ci, cname in enumerate(names):
        m = yt == ci
        if not m.any():
            continue
        armed = sorted(a for a in set(ar[m]) if a)
        _line(cname, m, ci,
              "   <- POOLED; read the arms below, not this" if len(armed) > 1 else "")
        if not armed:                      # no arms on this class (e.g. healthy)
            for s in sorted(set(sp[m])):
                _line(f"    └ {s:32s}"[:38], m & (sp == s), ci)
            if ci == healthy_idx:
                _false_fire_line("healthy FALSE-FIRE (any disease call)", m)
                for s in sorted(set(sp[m])):
                    _false_fire_line(f"    └ {s:32s}"[:38], m & (sp == s))
            continue
        for arm in armed:
            a = m & (ar == arm)
            note = {"necrosis": "  (diagnostic regression sentinel; not a release gate)",
                    "turgor": "  (off-morphology probe: information, not a gate)"}.get(arm, "")
            _line(f"  [{arm}]{'':30s}"[:38], a, ci, note)
            for s in sorted(set(sp[a])):
                _line(f"    └ {s:32s}"[:38], a & (sp == s), ci)

    # Between-species spread is what decides whether a pooled number means
    # anything. With k=2 species at SD 0.616 the 95% CI on a domain-level scorch
    # estimate is +-0.854 — wider than the unit interval.
    print()
    for ci, cname in enumerate(names):
        m = yt == ci
        if not m.any():
            continue
        per_sp = [float((ya[m & (sp == s)] == ci).mean()) for s in sorted(set(sp[m]))]
        k = len(per_sp)
        if k < 2:
            continue
        sd = float(np.std(per_sp, ddof=1))
        # Student's t, not 1.96. At k=2 the two differ by 6.5x, and a file whose
        # whole argument is "your interval is wider than you think" cannot
        # afford to understate its own by that much.
        crit = _T95.get(k - 1, 1.96)
        half = crit * sd / np.sqrt(k)
        verdict = "OK for a domain claim" if half <= 0.15 else "TOO FEW SPECIES for a domain claim"
        print(f"{cname:38s} k={k:2d} species, between-species SD={sd:.3f} "
              f"-> 95% CI +-{half:.3f} (t_{k-1}={crit:.3f})  {verdict}")
        if half > 0.15 and sd > 0:
            # Solved with z, since the k that satisfies this is large enough for
            # t -> z; quoting a t-based k here would be spurious precision.
            need = int(np.ceil((1.96 * sd / 0.15) ** 2))
            print(f"{'':38s} reaching +-0.15 needs k~{need} species at this SD")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="extracted source root (e.g. ext/flower_data)")
    ap.add_argument("--out", type=Path, default=Path("oodbench"))
    ap.add_argument("--per-class-cap", type=int, default=400)
    ap.add_argument("--min-clusters-per-class", type=int,
                    default=MIN_CLUSTERS_PER_CLASS)
    ap.add_argument(
        "--family-manifest", type=Path,
        help="exhaustive offline SIFT/RANSAC family CSV for large sources")
    ap.add_argument("--report", type=Path, help="preds.json to break down per species")
    ap.add_argument("--manifest", type=Path, help="benchmark manifest.csv (with --report)")
    args = ap.parse_args()
    if args.report:
        if not args.manifest:
            sys.exit("--report needs --manifest")
        report(args.report, args.manifest)
        return
    if not args.src:
        sys.exit("--src is required (or use --report)")
    build(args.src, args.out, args.per_class_cap, args.min_clusters_per_class,
          family_manifest=args.family_manifest)


if __name__ == "__main__":
    main()
