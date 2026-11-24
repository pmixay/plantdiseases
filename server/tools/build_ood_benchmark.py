#!/usr/bin/env python3
"""Build a family-deduplicated, never-trained benchmark for ``paired_eval``.

Two modes share one code path:

* ``--config sources.json`` declares any number of sources, their folder
  layouts, the condition-folder -> class mapping, the arms (the non-plant and
  difficult-plant arms included) and the rights record. This is how benchmark
  **v3** is built (``docs/DATASET_LABELING_PLAN.md``, "OOD benchmark v3").
* ``--src DIR`` is the legacy single-source build that produced the private v2
  sentinel: healthy ornamental leaves and one labelled disease arm from one
  Kaggle dataset, no non-plant rows. v2 is an ``unseen-domain regression
  benchmark``, not a general OOD benchmark, and reports must say so.

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
  those pseudo-replicates.  SHA dedup alone is not enough. A collector's own
  family ids (``acquisition_record_v1``) are honoured as a claim and still
  backstopped by both measures; they never switch the backstop off.
* **Species, session and arm are recorded and reported**, because a
  one-species/one-session result is not a class-general result. A per-class
  number that is really a per-species number is a trap.
* **A minimum independent-family count per class**, so a diagnostic cannot
  silently claim an image count that is mostly augmented copies.
* **Labels come from the classifier's taxonomy** (``classifier.DEFAULT_CLASS_NAMES``
  or a bundle's ``classes.json``); a mapping to any other name is refused.
* **Sources are checked against the notebook's deny-lists** before anything is
  written: a Kaggle source the training notebook could still download would
  turn the benchmark into training data by accident.

The existing private v2 artifact is a *diagnostic sentinel*, not a release
gate: its 31 scorch rows come from one studio domain and contain confirmed
rotated/noised pseudo-replicates.  Do not turn a benchmark into a release gate
until a genuinely independent, deployment-representative v3 exists.

The legacy source (`shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`,
MIT) is deliberately excluded from `KAGGLE_ORNAMENTAL_SLUGS` — see
`DATASET_LABELING_PLAN.md`, which rejects it as *training* data precisely
because its labels are unverified. That is fine here and not a contradiction:
label noise in a PAIRED comparison hits both models equally, and the models
disagreeing 32-0 on identical labels cannot be explained by the labels.

Config (v3)
-----------
``sources.json`` (a relative ``root`` / ``family_manifest`` resolves against
the config file's directory)::

    {
      "benchmark_slug": "owner/the-private-v3-dataset",
      "sources": [
        {
          "id": "np01",
          "root": "batches/nonplant_01",
          "origin": "first_party",
          "acquisition_batch_id": "np01",
          "layout": "session/condition",
          "license_id": "project-owned",
          "source_url": "plantvision://acquisition/np01",
          "creator": "collector-a",
          "rights_status": "project_owned_capture; P5 record np01",
          "release_eligible": true,
          "rights_verified_by": "reviewer-b",
          "conditions": {
            "empty_pot":     {"class": "not_a_plant"},
            "foliage_print": {"class": "not_a_plant"},
            "variegated":    {"class": "healthy", "arm": "difficult_plant"}
          }
        },
        {
          "id": "sc01",
          "root": "batches/scorch_01",
          "origin": "first_party",
          "acquisition_batch_id": "sc01",
          "layout": "session/species/condition",
          "family_manifest": "batches/scorch_01/families.csv",
          "license_id": "project-owned",
          "conditions": {
            "healthy": {"class": "healthy"},
            "scorch":  {"class": "leaf_scorch_underwatering"}
          },
          "species_arms": {"hibiscus": "necrosis", "ficus": "necrosis"}
        }
      ]
    }

``layout`` names the directory levels under ``root``, top down; images sit in
the last one. Tokens: ``condition`` (required), ``species``, ``session``,
``split`` and ``_`` (a level to ignore). A source may fix ``species`` or
``session`` as a constant instead. ``origin`` is ``kaggle`` (needs ``slug``,
which must be deny-listed in the notebook) or ``first_party`` (needs
``acquisition_batch_id``). A condition's ``arm`` defaults to blank for
``healthy``, ``non_plant`` for ``not_a_plant``, and the species' entry in
``species_arms`` for a disease class: declared, never inferred.
``retired_disease_species`` drops the diseased folders of a species and keeps
its healthy ones. ``family_manifest`` is a CSV of
``source_relative_path,capture_family_id,family_method`` covering every image
of the source, with one method: ``sift_ransac_v1`` (an offline exhaustive
audit, replaces the in-process SIFT pass) or ``acquisition_record_v1`` (the
collector's P2 ids, unioned with the pHash and SIFT backstops).

Usage
-----
::

    # v3, from declared sources:
    python -m server.tools.build_ood_benchmark --config sources.json --out oodbench

    # Legacy v2 rebuild from an already-extracted copy:
    python -m server.tools.build_ood_benchmark --src ext/flower_data --out oodbench

    # A source above the exhaustive safety cap needs the offline audit CSV:
    python -m server.tools.build_ood_benchmark --src ext/flower_data --out oodbench \\
        --family-manifest audited_families.csv

Then score it end-to-end (NOT Stage-2 argmax — the OOD AND-gate alone can move
ornamental recall ~20pp), with the manifest so the bootstrap resamples capture
families::

    python -m server.tools.paired_eval --bundle-a <incumbent> --bundle-b <candidate> \\
        --eval-dir oodbench --manifest oodbench/manifest.csv --provenance '' \\
        --out preds.json --run-bootstrap
    python -m server.tools.build_ood_benchmark --report preds.json --manifest oodbench/manifest.csv
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_SERVER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_NOTEBOOK = _SERVER_DIR / "train_notebook.ipynb"

# The two class names this tool gives a meaning to. Every class a source maps
# to, these included, is checked against the classifier's own class list at
# build time, so a renamed head class fails here instead of producing a
# benchmark folder no bundle can score.
HEALTHY_CLASS = "healthy"
NAP_CLASS = "not_a_plant"

# Legacy (v2) source condition folder -> our taxonomy. Deliberately small: only
# conditions that map UNAMBIGUOUSLY. 'yld' (yellow leaf disease), 'death_leaf',
# 'blight', 'early_blight', 'red_spot' and 'insect_bite' are dropped — guessing
# a mapping would put label noise into the one eval set whose whole job is to be
# trusted. A v3 source declares its own mapping in the config.
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
# The collector's own P2 family ids. A claim about the physical capture: it can
# join frames the image measures cannot see are siblings, and it never replaces
# those measures.
CLAIMED_FAMILY_METHOD = "acquisition_record_v1"
EXHAUSTIVE_FAMILY_METHOD = "exhaustive_sift_ransac_v1"
UNCERTIFIED_FAMILY_METHOD = "phash_only_NOT_CERTIFIED"
MIN_CLUSTERS_PER_CLASS = 60
SOURCE_DATASET_SLUG = "shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update"
SOURCE_DATASET_VERSION = "1"
SOURCE_LICENSE_ID = "MIT"
SOURCE_URL = f"https://www.kaggle.com/datasets/{SOURCE_DATASET_SLUG}/versions/{SOURCE_DATASET_VERSION}"
SOURCE_RIGHTS_STATUS = "dataset_page_declaration_only; per-image_rights_unverified"
LEGACY_LAYOUT = "split/species/condition"
LAYOUT_TOKENS = ("condition", "species", "session", "split", "_")
ORIGINS = ("kaggle", "first_party")

# v3 targets from DATASET_LABELING_PLAN.md. Targets, not refusals: the build
# prints what it misses and records it in build_info.json.
V3_SPECIES_RANGE = (8, 12)
V3_MAX_SPECIES_SHARE = 0.25
V3_MIN_SESSIONS_PER_SPECIES = 3
V3_FAMILIES_PER_SCORED_ARM = 100

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
# These two constants describe the LEGACY source; a v3 source declares
# `species_arms` / `retired_disease_species` in its config entry.
SPECIES_ARMS = {
    "hibiscus": "necrosis",       # dead brown tissue — what our class is trained on
}
DEFAULT_ARM = "unassigned"
# The two arms v2 never had. `non_plant` is a genuine non-plant in plant-like
# context (class `not_a_plant`); `difficult_plant` is a healthy plant the gate
# must neither reject nor call diseased (class `healthy`).
ARM_NON_PLANT = "non_plant"
ARM_DIFFICULT_PLANT = "difficult_plant"
NEGATIVE_ARMS = (ARM_NON_PLANT, ARM_DIFFICULT_PLANT)

# Arms whose diseased images are not reliably aligned to this project's class
# definition are dropped instead of being relabelled by assumption.
#
# Only the DISEASED images are dropped. Healthy banana_bush stays: it is
# correctly labelled, the species is an in-domain garden ornamental, and cutting
# it would shrink the false-fire arm for no gain.
RETIRED_DISEASE_ARMS = {"banana_bush"}

MANIFEST_FIELDS = [
    "filename", "class", "species", "source", "source_slug",
    "source_version", "source_relative_path", "license_id", "source_url",
    "rights_status", "release_eligible",
    "capture_family_id", "family_method", "source_family_size",
    "brown_fraction", "arm",
    # v3 additions. Readers that predate them ignore unknown columns.
    "negative_kind", "session", "source_id", "source_origin", "creator",
    "acquisition_batch_id", "rights_verified_by",
]

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
    claimed_ids: dict[Path, str] | None = None,
) -> list[list[Path]]:
    """Single-link capture families using pHash and geometric correspondences.

    ``family_ids`` is an exhaustive offline SIFT/RANSAC audit: it must cover
    every path and it replaces the in-process SIFT pass. ``claimed_ids`` is the
    collector's own family claim (P2): rows sharing an id are joined, rows
    without one are left alone, and pHash and SIFT still run over everything,
    because a claim can add a sibling the image measures miss and can never
    certify that two rows are independent.
    """
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

    if claimed_ids:
        first_claim: dict[str, int] = {}
        for i, (path, _hash) in enumerate(decoded):
            family = claimed_ids.get(path)
            if not family:
                continue
            if family in first_claim:
                union(i, first_claim[family])
            else:
                first_claim[family] = i

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
    claimed_ids: dict[Path, str] | None = None,
) -> list[Path]:
    """Keep one deterministic representative per independent capture family."""
    return [group[0] for group in cluster_paths(
        paths, threshold, robust, family_ids, claimed_ids)]


# ── Sources ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Condition:
    """What one condition folder of a source holds."""

    cls: str
    arm: str = ""        # "" = resolve by class / species (see ``_resolve_arm``)


@dataclass(frozen=True)
class SourceSpec:
    """One declared source: where it is, how it is laid out, whose it is."""

    source_id: str
    root: Path
    origin: str = "kaggle"
    layout: tuple[str, ...] = tuple(LEGACY_LAYOUT.split("/"))
    conditions: dict[str, Condition] = field(default_factory=dict)
    species_arms: dict[str, str] = field(default_factory=dict)
    retired_disease_species: frozenset[str] = frozenset()
    species: str = ""              # constant, when the layout has no species level
    session: str = ""              # constant, when the layout has no session level
    slug: str = ""
    version: str = ""
    license_id: str = ""
    source_url: str = ""
    creator: str = ""
    rights_status: str = ""
    release_eligible: bool = False
    rights_verified_by: str = ""
    acquisition_batch_id: str = ""
    family_manifest: Path | None = None

    @property
    def key(self) -> str:
        """Stable provenance name: the slug for a dataset, else the source id."""
        return self.slug or self.source_id


@dataclass(frozen=True)
class _Item:
    path: Path
    source: SourceSpec
    cls: str
    condition: str
    species: str
    session: str
    split: str
    arm: str
    rel: str             # POSIX path relative to the source root

    @property
    def unit(self) -> str:
        """What the class cap round-robins over: the species, or for a row
        without one (a non-plant) the kind of negative it is."""
        return self.species or self.condition


def legacy_source(src: Path, family_manifest: Path | None = None) -> SourceSpec:
    """The one source v2 was built from, as a declared spec."""
    return SourceSpec(
        source_id="", root=Path(src).resolve(), origin="kaggle",
        conditions={k: Condition(v) for k, v in CONDITION_MAP.items()},
        species_arms=dict(SPECIES_ARMS),
        retired_disease_species=frozenset(RETIRED_DISEASE_ARMS),
        slug=SOURCE_DATASET_SLUG, version=SOURCE_DATASET_VERSION,
        license_id=SOURCE_LICENSE_ID, source_url=SOURCE_URL,
        rights_status=SOURCE_RIGHTS_STATUS, release_eligible=False,
        family_manifest=(Path(family_manifest).resolve()
                         if family_manifest is not None else None))


def load_class_names(classes_json: Path | None = None) -> list[str]:
    """The label taxonomy: a bundle's ``classes.json`` or the classifier default."""
    if classes_json is not None:
        names = json.loads(Path(classes_json).read_text(encoding="utf-8")).get("class_names")
        if (not isinstance(names, list) or not names
                or any(not isinstance(n, str) or not n for n in names)):
            raise RuntimeError(f"{classes_json}: class_names must be a non-empty string list")
        return list(names)
    if str(_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVER_DIR))
    from classifier import DEFAULT_CLASS_NAMES  # noqa: PLC0415

    return list(DEFAULT_CLASS_NAMES)


def _text(raw: dict, key: str, where: str, default: str = "") -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise RuntimeError(f"{where}: {key!r} must be a string")
    return value.strip()


def _source_from_config(raw: dict, base: Path, index: int) -> SourceSpec:
    where = f"sources[{index}]"
    if not isinstance(raw, dict):
        raise RuntimeError(f"{where}: must be an object")
    known = {
        "id", "root", "origin", "layout", "conditions", "species_arms",
        "retired_disease_species", "species", "session", "slug", "version",
        "license_id", "source_url", "creator", "rights_status", "release_eligible",
        "rights_verified_by", "acquisition_batch_id", "family_manifest"}
    unknown = sorted(set(raw) - known)
    if unknown:
        # A misspelt key (`licence_id`, `specie_arms`) would otherwise be a
        # silently blank column on every row of an eval set.
        raise RuntimeError(f"{where}: unknown keys {unknown}")
    source_id = _text(raw, "id", where)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", source_id):
        raise RuntimeError(
            f"{where}: 'id' must be 1-32 characters of [A-Za-z0-9_-]; it prefixes "
            f"every output filename")
    where = f"source {source_id!r}"
    root_raw = _text(raw, "root", where)
    if not root_raw:
        raise RuntimeError(f"{where}: 'root' is required")
    origin = _text(raw, "origin", where)
    if origin not in ORIGINS:
        raise RuntimeError(f"{where}: 'origin' must be one of {list(ORIGINS)}")
    layout = tuple(t.strip() for t in _text(raw, "layout", where, LEGACY_LAYOUT).split("/"))
    conditions_raw = raw.get("conditions")
    if not isinstance(conditions_raw, dict) or not conditions_raw:
        raise RuntimeError(f"{where}: 'conditions' must map folder names to classes")
    conditions: dict[str, Condition] = {}
    for folder, spec in conditions_raw.items():
        if not isinstance(spec, dict) or set(spec) - {"class", "arm"}:
            raise RuntimeError(
                f"{where}: condition {folder!r} must be an object with 'class' and "
                f"an optional 'arm'")
        name = str(folder).strip().lower()
        if not name or name in conditions:
            raise RuntimeError(f"{where}: empty or duplicate condition folder {folder!r}")
        conditions[name] = Condition(
            _text(spec, "class", f"{where} condition {folder!r}"),
            _text(spec, "arm", f"{where} condition {folder!r}"))
    arms_raw = raw.get("species_arms", {})
    if not isinstance(arms_raw, dict) or any(
            not isinstance(v, str) or not v.strip() for v in arms_raw.values()):
        raise RuntimeError(f"{where}: 'species_arms' must map species to arm names")
    retired = raw.get("retired_disease_species", [])
    if not isinstance(retired, list) or any(not isinstance(s, str) for s in retired):
        raise RuntimeError(f"{where}: 'retired_disease_species' must be a list of names")
    eligible = raw.get("release_eligible", False)
    if not isinstance(eligible, bool):
        raise RuntimeError(f"{where}: 'release_eligible' must be true or false")
    family_raw = _text(raw, "family_manifest", where)
    return SourceSpec(
        source_id=source_id, root=(base / root_raw).resolve(), origin=origin,
        layout=layout, conditions=conditions,
        species_arms={str(k): v.strip() for k, v in arms_raw.items()},
        retired_disease_species=frozenset(retired),
        species=_text(raw, "species", where), session=_text(raw, "session", where),
        slug=_text(raw, "slug", where).lower(), version=_text(raw, "version", where),
        license_id=_text(raw, "license_id", where),
        source_url=_text(raw, "source_url", where), creator=_text(raw, "creator", where),
        rights_status=_text(raw, "rights_status", where), release_eligible=eligible,
        rights_verified_by=_text(raw, "rights_verified_by", where),
        acquisition_batch_id=_text(raw, "acquisition_batch_id", where),
        family_manifest=(base / family_raw).resolve() if family_raw else None)


def load_config(path: Path) -> tuple[list[SourceSpec], str]:
    """Read ``sources.json`` -> (sources, the slug the result is published under)."""
    path = Path(path).resolve()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or set(doc) - {"benchmark_slug", "sources"}:
        raise RuntimeError(f"{path}: expected an object with 'sources' and 'benchmark_slug'")
    raw_sources = doc.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RuntimeError(f"{path}: 'sources' must be a non-empty list")
    slug = doc.get("benchmark_slug", "")
    if not isinstance(slug, str):
        raise RuntimeError(f"{path}: 'benchmark_slug' must be a string")
    return ([_source_from_config(raw, path.parent, i) for i, raw in enumerate(raw_sources)],
            slug.strip().lower())


def _validate_sources(sources: list[SourceSpec], class_names: list[str]) -> None:
    if not sources:
        raise ValueError("at least one source is required")
    ids = [s.source_id for s in sources]
    if len(set(ids)) != len(ids) or (len(sources) > 1 and not all(ids)):
        raise RuntimeError(f"source ids must be unique and non-empty: {ids}")
    for a_i, a in enumerate(sources):
        for b in sources[a_i + 1:]:
            if a.root == b.root or a.root in b.root.parents or b.root in a.root.parents:
                # One photograph reachable from two sources is two rows with two
                # rights records for one capture.
                raise RuntimeError(
                    f"sources {a.source_id!r} and {b.source_id!r} share or nest roots")
    for s in sources:
        where = f"source {s.source_id!r}"
        if not s.root.is_dir():
            raise ValueError(f"source directory does not exist: {s.root}")
        bad = [t for t in s.layout if t not in LAYOUT_TOKENS]
        named = [t for t in s.layout if t != "_"]
        if bad or len(set(named)) != len(named) or "condition" not in named:
            raise RuntimeError(
                f"{where}: layout {'/'.join(s.layout)!r} must be '/'-joined tokens from "
                f"{list(LAYOUT_TOKENS)}, each at most once, with 'condition'")
        for token, constant in (("species", s.species), ("session", s.session)):
            if constant and token in s.layout:
                raise RuntimeError(
                    f"{where}: {token!r} is both a layout level and a constant")
        if s.origin == "kaggle" and not re.fullmatch(r"[^/\s]+/[^/\s]+", s.slug):
            raise RuntimeError(f"{where}: a kaggle source needs 'slug' as owner/name")
        if s.origin == "first_party":
            if not s.acquisition_batch_id:
                raise RuntimeError(
                    f"{where}: a first_party source needs 'acquisition_batch_id' (the "
                    f"P5 record is its rights basis and its independence claim)")
            if s.slug:
                raise RuntimeError(f"{where}: a first_party source has no 'slug'")
        if s.release_eligible and not (s.rights_verified_by and s.rights_status):
            raise RuntimeError(
                f"{where}: release_eligible=true needs 'rights_verified_by' (a person) "
                f"and 'rights_status' (what was verified)")
        for folder, cond in s.conditions.items():
            if cond.cls not in class_names:
                raise RuntimeError(
                    f"{where}: condition {folder!r} maps to {cond.cls!r}, which is not "
                    f"a classifier class {class_names}")
            if cond.arm == ARM_NON_PLANT and cond.cls != NAP_CLASS:
                raise RuntimeError(
                    f"{where}: arm {ARM_NON_PLANT!r} is for class {NAP_CLASS!r} only "
                    f"(condition {folder!r})")
            if cond.arm == ARM_DIFFICULT_PLANT and cond.cls != HEALTHY_CLASS:
                raise RuntimeError(
                    f"{where}: arm {ARM_DIFFICULT_PLANT!r} is a HEALTHY plant the gate "
                    f"must not reject; condition {folder!r} maps to {cond.cls!r}")
            if cond.cls == NAP_CLASS and cond.arm not in ("", ARM_NON_PLANT):
                raise RuntimeError(
                    f"{where}: a {NAP_CLASS!r} condition is arm {ARM_NON_PLANT!r} "
                    f"(condition {folder!r} declares {cond.arm!r})")
            if (cond.cls != NAP_CLASS and "species" not in s.layout and not s.species):
                raise RuntimeError(
                    f"{where}: condition {folder!r} is a plant class but the source "
                    f"declares no species (layout level or constant)")


def _resolve_arm(source: SourceSpec, cond: Condition, species: str) -> str:
    if cond.arm:
        return cond.arm
    if cond.cls == HEALTHY_CLASS:
        return ""
    if cond.cls == NAP_CLASS:
        return ARM_NON_PLANT
    return source.species_arms.get(species, DEFAULT_ARM)


def _scan_source(source: SourceSpec) -> tuple[list[_Item], Counter]:
    """Walk a source by its declared layout -> (mapped items, skipped folders)."""
    items: list[_Item] = []
    skipped: Counter = Counter()

    def walk(directory: Path, depth: int, ctx: dict[str, str]) -> None:
        if depth == len(source.layout):
            cond = source.conditions.get(ctx["condition"].lower())
            files = sorted(f for f in directory.iterdir()
                           if f.is_file() and f.suffix.lower() in IMG_EXTS)
            if cond is None:
                skipped[ctx["condition"].lower()] += len(files)
                return
            species = ctx.get("species", source.species)
            for f in files:
                items.append(_Item(
                    path=f, source=source, cls=cond.cls,
                    condition=ctx["condition"].lower(), species=species,
                    session=ctx.get("session", source.session),
                    split=ctx.get("split", ""),
                    arm=_resolve_arm(source, cond, species),
                    rel=f.relative_to(source.root).as_posix()))
            return
        token = source.layout[depth]
        for sub in sorted(p for p in directory.iterdir() if p.is_dir()):
            walk(sub, depth + 1, ctx if token == "_" else {**ctx, token: sub.name})

    walk(source.root, 0, {})
    return items, skipped


def _load_family_manifest(path: Path, src: Path) -> tuple[dict[Path, str], str]:
    """Load a source's family assignment -> ({image: family id}, method)."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    required = {"source_relative_path", "capture_family_id", "family_method"}
    if not rows or required - set(rows[0]):
        raise RuntimeError(
            f"family manifest must contain {sorted(required)} and at least one row")
    allowed = (PRECOMPUTED_FAMILY_METHOD, CLAIMED_FAMILY_METHOD)
    out: dict[Path, str] = {}
    methods: set[str] = set()
    for line, row in enumerate(rows, start=2):
        raw = (row.get("source_relative_path") or "").strip().replace("\\", "/")
        rel = PurePosixPath(raw)
        family = (row.get("capture_family_id") or "").strip()
        method = (row.get("family_method") or "").strip()
        if (not raw or rel.is_absolute() or ".." in rel.parts or not family
                or method not in allowed):
            raise RuntimeError(
                f"invalid family manifest line {line}: path={raw!r}, "
                f"family={family!r}, method={method!r}")
        local = src.joinpath(*rel.parts).resolve()
        if src != local and src not in local.parents:
            raise RuntimeError(f"family manifest path escapes source at line {line}: {raw!r}")
        if local in out:
            raise RuntimeError(f"duplicate family-manifest path at line {line}: {raw!r}")
        out[local] = family
        methods.add(method)
    if len(methods) != 1:
        raise RuntimeError(
            f"family manifest {path} mixes methods {sorted(methods)}; one audit, one method")
    return out, methods.pop()


# ── Deny-lists ───────────────────────────────────────────────────────


def _notebook_literal(source: str, name: str):
    """Evaluate a top-level ``NAME = {...}`` literal out of a notebook cell."""
    match = re.search(rf"^{name} = \{{.*?^\}}", source, re.S | re.M)
    if match is None:
        raise RuntimeError(f"the notebook defines no {name} literal")
    return ast.literal_eval(match.group(0).split("=", 1)[1].strip())


def load_deny_lists(notebook: Path = DEFAULT_NOTEBOOK) -> dict:
    """Read the training notebook's benchmark-contamination lists.

    The notebook is the only place the lists are enforced (§2,
    ``_assert_source_allowed``), so it is the only copy; this reads it rather
    than keeping a second one that could drift.
    """
    notebook = Path(notebook)
    if not notebook.is_file():
        raise RuntimeError(
            f"training notebook not found at {notebook}; pass --notebook. The build "
            f"is refused without it: an un-denied source can be trained on")
    cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
    source = next((s for s in ("".join(c["source"]) for c in cells)
                   if re.search(r"^KAGGLE_DENY_SLUGS = \{", s, re.M)), None)
    if source is None:
        raise RuntimeError(f"{notebook}: no cell defines KAGGLE_DENY_SLUGS")
    slugs = _notebook_literal(source, "KAGGLE_DENY_SLUGS")
    folders = _notebook_literal(source, "KAGGLE_DENY_FOLDERS")
    benchmarks = _notebook_literal(source, "KAGGLE_BENCHMARK_SLUGS")
    return {
        "slugs": {str(s).lower() for s in slugs},
        "folders": {str(k).lower(): tuple(str(x).lower() for x in v)
                    for k, v in folders.items()},
        "benchmark_slugs": {str(s).lower() for s in benchmarks},
        "notebook": str(notebook),
    }


def check_deny_lists(items: list[_Item], deny: dict, benchmark_slug: str = "") -> list[str]:
    """Refuse a build whose sources the training notebook could still pull.

    Returns the per-source verdict lines for ``build_info.json``. A first-party
    batch has no slug to deny: its disjointness from the supplement is an
    acquisition property (P5), checked afterwards by
    ``filter_supplement.py --bench``, and the line says so instead of passing it.
    """
    problems: list[str] = []
    verdicts: list[str] = []
    by_source: dict[str, list[_Item]] = defaultdict(list)
    for item in items:
        by_source[item.source.source_id].append(item)
    for source_id, rows in sorted(by_source.items()):
        source = rows[0].source
        label = source_id or source.key
        if source.origin != "kaggle":
            verdicts.append(
                f"{label}: first_party batch {source.acquisition_batch_id!r}; no slug to "
                f"deny, run filter_supplement.py --bench against the supplement")
            continue
        slug = source.slug.lower()
        if slug in deny["slugs"]:
            verdicts.append(f"{label}: {slug} is in KAGGLE_DENY_SLUGS")
            continue
        denied = deny["folders"].get(slug, ())
        loose = [r.rel for r in rows if not any(
            d in r.rel.lower() or d in re.sub(r"[^a-z0-9]+", "_", r.rel.lower())
            for d in denied)]
        if denied and not loose:
            verdicts.append(f"{label}: every used folder of {slug} is in KAGGLE_DENY_FOLDERS")
            continue
        problems.append(
            f"{slug} (source {label!r}) is not deny-listed"
            + (f"; {len(loose)} used file(s) fall outside its KAGGLE_DENY_FOLDERS entry "
               f"{list(denied)}, e.g. {loose[:3]}" if denied else ""))
    if benchmark_slug:
        if benchmark_slug in deny["benchmark_slugs"]:
            verdicts.append(f"benchmark slug {benchmark_slug} is in KAGGLE_BENCHMARK_SLUGS")
        else:
            problems.append(
                f"benchmark slug {benchmark_slug} is not in KAGGLE_BENCHMARK_SLUGS")
    if problems:
        raise RuntimeError(
            "refusing to build: " + "; ".join(problems) + f". Add the entries to "
            f"{deny['notebook']} §2 first (DATASET_LABELING_PLAN.md: the deny-list "
            f"entries exist before the first row lands), then rebuild")
    return verdicts


# ── Build ────────────────────────────────────────────────────────────


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", part).strip("-") or "x"


def v3_target_notes(rows: list[dict]) -> list[str]:
    """What a built manifest still misses of the v3 targets (never a refusal)."""
    notes: list[str] = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["class"], row["arm"])].append(row)
    for (cls, arm), members in sorted(groups.items()):
        label = f"{cls}[{arm}]" if arm else cls
        n = len(members)
        if n < V3_FAMILIES_PER_SCORED_ARM:
            notes.append(
                f"{label}: {n} families, under the ~{V3_FAMILIES_PER_SCORED_ARM} a "
                f"family-level floor needs; advisory only")
        if arm in NEGATIVE_ARMS:
            continue
        by_species = Counter(r["species"] for r in members)
        if cls not in (HEALTHY_CLASS, NAP_CLASS):
            low, high = V3_SPECIES_RANGE
            if not low <= len(by_species) <= high:
                notes.append(f"{label}: {len(by_species)} species, target {low}-{high}")
            top, top_n = by_species.most_common(1)[0]
            if len(by_species) > 1 and top_n > V3_MAX_SPECIES_SHARE * n:
                notes.append(
                    f"{label}: {top} holds {top_n}/{n} families, over the "
                    f"{V3_MAX_SPECIES_SHARE:.0%} species share target")
        thin = sorted(
            sp for sp in by_species
            if len({r["session"] for r in members if r["species"] == sp and r["session"]})
            < V3_MIN_SESSIONS_PER_SPECIES)
        if thin:
            notes.append(
                f"{label}: under {V3_MIN_SESSIONS_PER_SPECIES} recorded sessions for "
                f"{len(thin)} species ({', '.join(thin[:6])}{', ...' if len(thin) > 6 else ''})")
    return notes


def build(src: Path, out: Path, per_class_cap: int,
          min_clusters_per_class: int = MIN_CLUSTERS_PER_CLASS,
          *, robust_dedup: bool = True,
          family_manifest: Path | None = None,
          deny_lists: dict | None = None) -> None:
    """Legacy single-source (v2) build: both mapped classes are required."""
    src = Path(src).resolve()
    if not src.is_dir():
        raise ValueError(f"source directory does not exist: {src}")
    build_benchmark(
        [legacy_source(src, family_manifest)], out, per_class_cap,
        min_clusters_per_class, robust_dedup=robust_dedup, deny_lists=deny_lists)


def build_benchmark(
    sources: list[SourceSpec], out: Path, per_class_cap: int,
    min_clusters_per_class: int = MIN_CLUSTERS_PER_CLASS,
    *, robust_dedup: bool = True,
    class_names: list[str] | None = None,
    deny_lists: dict | None = None,
    benchmark_slug: str = "",
) -> None:
    """Build a capped, class-wide family-deduped benchmark from declared sources.

    Every class a source's ``conditions`` map to is REQUIRED: it must reach
    ``min_clusters_per_class`` independent families or nothing is written. A
    build of the non-plant arm alone is therefore a config that maps only
    non-plant conditions, not a flag.
    """
    out = Path(out).resolve()
    class_names = load_class_names() if class_names is None else list(class_names)
    if per_class_cap <= 0 or min_clusters_per_class <= 0:
        raise ValueError("per_class_cap and min_clusters_per_class must be positive")
    _validate_sources(sources, class_names)
    for s in sources:
        if s.root == out or s.root in out.parents or out in s.root.parents:
            raise ValueError("--src and --out must be separate, non-nested directories")

    items: list[_Item] = []
    audited: dict[Path, str] = {}        # sift_ransac_v1: replaces the SIFT pass
    claimed: dict[Path, str] = {}        # acquisition_record_v1: joins, never replaces
    for s in sources:
        found, skipped = _scan_source(s)
        if skipped:
            print(f"  source {s.source_id or s.key}: unmapped condition folders skipped "
                  f"{dict(sorted(skipped.items()))}")
        if s.family_manifest is not None:
            ids, method = _load_family_manifest(s.family_manifest, s.root)
            if method == CLAIMED_FAMILY_METHOD:
                missing = [i.rel for i in found if i.path.resolve() not in ids]
                if missing:
                    raise RuntimeError(
                        f"source {s.source_id!r}: {CLAIMED_FAMILY_METHOD} manifest misses "
                        f"{len(missing)} image(s), e.g. {missing[:5]}")
                claimed.update(ids)
            else:
                audited.update(ids)
        items.extend(found)
    by_path = {i.path: i for i in items}
    resolved = {i.path: i.path.resolve() for i in items}
    precomputed = ({p: audited.get(resolved[p], "") for p in by_path}
                   if audited else None)
    claimed_ids = {p: claimed[resolved[p]] for p in by_path if resolved[p] in claimed}

    deny_verdicts = (check_deny_lists(items, deny_lists, benchmark_slug)
                     if deny_lists is not None else [])

    expected_classes = {c.cls for s in sources for c in s.conditions.values()}
    present = {i.cls for i in items}
    if expected_classes - present:
        raise RuntimeError(
            f"source has no mapped rows for {sorted(expected_classes - present)}")

    # Dedup across the WHOLE class, not once per species or per source. A
    # re-uploaded photo under two species is still one capture and must not
    # count twice.
    raw_counts: Counter = Counter((i.cls, i.unit) for i in items)
    unique_by_class: dict[str, dict[str, list[Path]]] = {}
    class_hashes: dict[str, list[tuple[Path, object]]] = {}
    family_size_by_path: dict[Path, int] = {}
    for cls in sorted(present):
        candidates = []
        retired: Counter = Counter()
        for item in (i for i in items if i.cls == cls):
            if cls != HEALTHY_CLASS and item.species in item.source.retired_disease_species:
                retired[item.species] += 1
                continue
            candidates.append(item.path)
        for species, n in sorted(retired.items()):
            print(f"  {species:28s} RETIRED for {cls} — {n} files skipped")
        families = cluster_paths(
            candidates, robust=robust_dedup,
            family_ids=({p: precomputed[p] for p in candidates}
                        if precomputed is not None else None),
            claimed_ids=claimed_ids or None)
        unique = [family[0] for family in families]
        for family in families:
            family_size_by_path[family[0]] = len(family)
        regrouped: dict[str, list[Path]] = defaultdict(list)
        for p in unique:
            regrouped[by_path[p].unit].append(p)
        unique_by_class[cls] = dict(regrouped)
        class_hashes[cls] = [(p, h) for p in unique if (h := _phash(p)) is not None]

    # Contradictory labels within any duplicate signal are not "dedup": they
    # are a benchmark-integrity error. Stop instead of keeping whichever class
    # sorts first. Same basenames in different class directories remain fine
    # when the content differs; downstream joins key on class/path.
    def _same_declared_family(pa: Path, pb: Path) -> bool:
        if precomputed is not None and precomputed.get(pa) == precomputed.get(pb):
            return True
        return pa in claimed_ids and claimed_ids.get(pa) == claimed_ids.get(pb)

    conflicts = []
    cross_class_feature_cache: dict[Path, tuple[object, object]] = {}
    classes = sorted(class_hashes)
    cross_pairs = sum(
        len(class_hashes[a]) * len(class_hashes[b])
        for i, a in enumerate(classes) for b in classes[i + 1:])
    if (robust_dedup and precomputed is None
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
                    if (robust_dedup and precomputed is None
                            and distance > PHASH_MAX_HAMMING):
                        inliers = _sift_homography_inliers(
                            pa, pb, cross_class_feature_cache)
                    if distance <= PHASH_MAX_HAMMING \
                            or inliers >= SIFT_MIN_HOMOGRAPHY_INLIERS \
                            or _same_declared_family(pa, pb):
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
    for cls, by_unit in sorted(unique_by_class.items()):
        queues = {s: deque(sorted(v)) for s, v in sorted(by_unit.items()) if v}
        chosen: dict[str, list[Path]] = defaultdict(list)
        total = 0
        while queues and total < per_class_cap:
            for unit in list(queues):
                if total >= per_class_cap:
                    break
                q = queues[unit]
                chosen[unit].append(q.popleft())
                total += 1
                if not q:
                    del queues[unit]
        selected[cls] = dict(chosen)

    counts = {c: sum(len(v) for v in selected.get(c, {}).values())
              for c in expected_classes}
    thin = {c: n for c, n in counts.items() if n < min_clusters_per_class}
    if thin:
        raise RuntimeError(
            f"under {min_clusters_per_class} independent capture families; "
            f"diagnostic sample size is not credible: {thin}")

    if precomputed is not None:
        family_method = PRECOMPUTED_FAMILY_METHOD
    else:
        family_method = EXHAUSTIVE_FAMILY_METHOD if robust_dedup else UNCERTIFIED_FAMILY_METHOD
    multi = len(sources) > 1 or bool(sources[0].source_id)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for cls, by_unit in sorted(selected.items()):
        print(f"\n{cls}:")
        (out / cls).mkdir(parents=True, exist_ok=True)
        for unit, take in sorted(by_unit.items()):
            browns = []
            arms = set()
            for f in take:
                item = by_path[f]
                source = item.source
                if multi:
                    parts = [source.source_id, item.split, item.session,
                             item.species, item.condition, f.stem]
                    name = "__".join(_safe(p) for p in parts if p)
                else:
                    # The v2 naming, kept so a legacy rebuild joins to old reports.
                    name = f"{item.split or 'x'}__{item.species}__{f.stem}"
                dst = out / cls / f"{name}{f.suffix.lower()}"
                if dst.exists():
                    raise RuntimeError(f"destination collision: {dst}")
                shutil.copy(f, dst)
                bf = brown_fraction(dst)
                if bf is not None:
                    browns.append(bf)
                arms.add(item.arm)
                if precomputed is not None:
                    family_id, method = precomputed[f], family_method
                elif f in claimed_ids:
                    family_id = claimed_ids[f]
                    method = f"{CLAIMED_FAMILY_METHOD}+{family_method}"
                else:
                    family_id = hashlib.sha256(
                        (source.key + ":" + item.rel).encode()).hexdigest()[:16]
                    method = family_method
                rows.append({
                    "filename": f"{cls}/{dst.name}", "class": cls,
                    "species": item.species, "source": source.key,
                    "source_slug": source.slug,
                    "source_version": source.version,
                    "source_relative_path": item.rel,
                    "license_id": source.license_id, "source_url": source.source_url,
                    "rights_status": source.rights_status,
                    "release_eligible": "true" if source.release_eligible else "false",
                    "capture_family_id": family_id,
                    "family_method": method,
                    "source_family_size": family_size_by_path[f],
                    "brown_fraction": "" if bf is None else f"{bf:.4f}",
                    "arm": item.arm,
                    "negative_kind": item.condition if item.arm in NEGATIVE_ARMS else "",
                    "session": item.session,
                    "source_id": source.source_id,
                    "source_origin": source.origin,
                    "creator": source.creator,
                    "acquisition_batch_id": source.acquisition_batch_id,
                    "rights_verified_by": source.rights_verified_by,
                })
            med = sorted(browns)[len(browns) // 2] if browns else None
            raw_n = raw_counts[(cls, unit)]
            uniq_n = len(unique_by_class[cls].get(unit, []))
            arm = "/".join(sorted(arms))
            print(f"  {unit:28s} {raw_n:5d} files -> {uniq_n:4d} families -> "
                  f"{len(take):4d} kept   brown_median="
                  f"{'n/a' if med is None else f'{med:.3f}'}  arm={arm}")
            if DEFAULT_ARM in arms:
                print(f"    WARN: {unit!r} is not in SPECIES_ARMS; inspect and assign it")

    keys = [r["filename"] for r in rows]
    dupes = [k for k, n in Counter(keys).items() if n != 1]
    if dupes:
        raise RuntimeError(f"duplicate class/path manifest keys: {dupes[:12]}")
    # paired_eval joins families on the path WITHOUT its suffix, so `a.jpg` and
    # `a.png` in one class would be one key there and two rows here.
    stems = Counter(PurePosixPath(k).with_suffix("").as_posix() for k in keys)
    clash = [k for k, n in stems.items() if n != 1]
    if clash:
        raise RuntimeError(f"manifest keys differ only by file suffix: {clash[:12]}")
    # §6 and filter_supplement both read one row as one independent family.
    family_dupes = [k for k, n in Counter(r["capture_family_id"] for r in rows).items()
                    if n != 1 or not k]
    if family_dupes:
        raise RuntimeError(
            f"capture_family_id is blank or repeats across rows: {family_dupes[:12]}")
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(rows)

    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*")
               if p.is_file() and p.suffix.lower() in IMG_EXTS}
    if set(keys) != on_disk or len(keys) != len(on_disk):
        raise RuntimeError("manifest/image class-path keys disagree after build")

    notes = v3_target_notes(rows)
    info = {
        "tool": "server/tools/build_ood_benchmark.py",
        "benchmark_slug": benchmark_slug,
        "class_names": class_names,
        "family_method": family_method,
        "constants": {
            "phash_max_hamming": PHASH_MAX_HAMMING,
            "sift_min_homography_inliers": SIFT_MIN_HOMOGRAPHY_INLIERS,
            "min_clusters_per_class": min_clusters_per_class,
            "per_class_cap": per_class_cap},
        "sources": [{
            "id": s.source_id, "origin": s.origin, "slug": s.slug, "version": s.version,
            "layout": "/".join(s.layout), "license_id": s.license_id,
            "acquisition_batch_id": s.acquisition_batch_id,
            "release_eligible": s.release_eligible,
            "conditions": {k: {"class": c.cls, "arm": c.arm}
                           for k, c in sorted(s.conditions.items())},
        } for s in sources],
        "families": {
            f"{cls}[{arm}]" if arm else cls: n
            for (cls, arm), n in sorted(Counter(
                (r["class"], r["arm"]) for r in rows).items())},
        "deny_list_check": deny_verdicts or "NOT RUN",
        "v3_target_notes": notes,
    }
    (out / "build_info.json").write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    kind = "never-trained benchmark" if multi else "unseen-domain regression benchmark"
    print(f"\nwrote {kind} {out} — {len(rows)} images")
    for cls in sorted(expected_classes):
        n_sp = len(selected.get(cls, {}))
        if n_sp < 3 and cls != NAP_CLASS:
            print(f"WARN: {cls} spans only {n_sp} species — report PER SPECIES and do "
                  f"not read a class-level delta as a domain-level one.")
    for note in notes:
        print(f"v3 target: {note}")
    for verdict in deny_verdicts:
        print(f"deny-list: {verdict}")


# ── Report ───────────────────────────────────────────────────────────

_ARM_NOTES = {
    "necrosis": "  (diagnostic regression sentinel; not a release gate)",
    "turgor": "  (off-morphology probe: information, not a gate)",
    ARM_NON_PLANT: "  (read ACCEPTED AS PLANT below: that is the harm)",
    ARM_DIFFICULT_PLANT: "  (read FALSE-REJECT and FALSE-FIRE below)",
}


def report(preds_path: Path, manifest: Path) -> dict:
    """Per-arm, per-species breakdown of a preds.json produced by paired_eval.

    Prints the table and returns the same numbers as
    ``{"family_verified": bool, "rows": [{class, arm, unit, stat, a, b, n}]}``.
    """
    import numpy as np

    d = json.loads(preds_path.read_text())
    if "items" not in d:
        sys.exit("preds.json has no `items` — re-run paired_eval (it records image paths now)")
    with open(manifest, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # Key on ``class/name``, NOT the bare basename: the same species+stem exists
    # under two classes (``hibiscus__test1.jpg`` is both a healthy and a scorch
    # file), so a basename key silently overwrites one class with the other and
    # the arm breakdown comes out attached to the wrong rows.
    def _key(p: str) -> str:
        parts = Path(p).parts
        return "/".join(parts[-2:]) if len(parts) >= 2 else str(p)
    by_key = {_key(r["filename"]): r for r in rows}
    unlisted = [i for i in d["items"] if _key(i) not in by_key]
    if unlisted:
        # These used to be pooled under species "?" and counted in every class
        # total: a wrong manifest then looked like a species nobody named.
        sys.exit(f"{len(unlisted)} scored image(s) have no row in {manifest} — wrong "
                 f"manifest, or an image the benchmark never listed: {unlisted[:5]}")
    scored = [by_key[_key(i)] for i in d["items"]]
    names = d["class_names"]
    yt = np.array(d["y_true"])
    ya = np.array(d["y_pred_a"])
    yb = np.array(d.get("y_pred_b") or d["y_pred_a"])
    la, lb = d.get("label_a", "a"), d.get("label_b", "b")
    ar = np.array([(r.get("arm") or "") for r in scored])
    # A negative-arm row is told apart by WHAT it is (an empty pot, a variegated
    # leaf); everything else by its species.
    unit = np.array([(r.get("negative_kind") or r.get("species") or "?") for r in scored])
    species = np.array([(r.get("species") or "") for r in scored])

    families = [(r.get("capture_family_id") or "").strip() for r in scored]
    family_verified = all(families) and len(set(families)) == len(families)
    if not family_verified:
        print("WARN: the manifest has no unique capture_family_id per scored row; every "
              "n below is a NOMINAL IMAGE count, not an independent-family count")

    disease_idx = [i for i, n in enumerate(names) if n not in (HEALTHY_CLASS, NAP_CLASS)]
    healthy_idx = names.index(HEALTHY_CLASS) if HEALTHY_CLASS in names else None
    nap_idx = names.index(NAP_CLASS) if NAP_CLASS in names else None
    plant_idx = [i for i in range(len(names)) if i != nap_idx]
    reject_idx = [-1] + ([nap_idx] if nap_idx is not None else [])
    out_rows: list[dict] = []

    def _record(cname, arm, unit_name, stat, a, b, k) -> None:
        out_rows.append({"class": cname, "arm": arm, "unit": unit_name, "stat": stat,
                         "a": a, "b": b, "n": int(k.sum())})

    def _line(label: str, k, ci: int, where: tuple, note: str = "") -> None:
        ra, rb = float((ya[k] == ci).mean()), float((yb[k] == ci).mean())
        aa, ab = int((ya[k] == -1).sum()), int((yb[k] == -1).sum())
        print(f"{label:38s} {ra:9.3f} {rb:9.3f} {rb - ra:+8.3f} {int(k.sum()):4d}"
              f"   {aa:6d} {ab:6d}{note}")
        _record(*where, "recall", ra, rb, k)

    def _rate_line(label: str, k, hit: list[int], where: tuple, stat: str) -> None:
        fa = float(np.isin(ya[k], hit).mean())
        fb = float(np.isin(yb[k], hit).mean())
        print(f"{label:38s} {fa:9.3f} {fb:9.3f} {fb - fa:+8.3f} {int(k.sum()):4d}")
        _record(*where, stat, fa, fb, k)

    def _breakdown(cname: str, m, title: str, hit: list[int], stat: str) -> None:
        """One rate for the class, then per arm and per species/kind."""
        _rate_line(title, m, hit, (cname, "*", "*"), stat)
        arm_values = sorted(set(ar[m]))
        for arm in arm_values:
            a = m & (ar == arm)
            if len(arm_values) > 1:
                _rate_line(f"  [{arm or 'plain'}]{'':30s}"[:38], a, hit,
                           (cname, arm, "*"), stat)
            for s in sorted(set(unit[a])):
                _rate_line(f"    └ {s:32s}"[:38], a & (unit == s), hit,
                           (cname, arm, s), stat)

    # An abstention (-1: blur gate, OOD gate) is scored WRONG for recall and is
    # coverage loss; it is never a false fire. The healthy rows therefore carry
    # separate statistics: recall of ``healthy`` (which an abstention lowers),
    # the FALSE-FIRE rate (a disease call on a healthy image — the user-facing
    # harm, which an abstention does not commit) and the FALSE-REJECT rate (the
    # plant was turned away). Reading only the first one made a model that
    # abstains on half its healthy images indistinguishable from one that calls
    # half of them scorched.
    #
    # On a NON-PLANT the reading flips: a `not_a_plant` verdict and an
    # abstention are both the right outcome (no diagnosis was issued), so its
    # recall line UNDERSTATES the gate and the number to read is ACCEPTED AS
    # PLANT. preds.json keeps no per-row abstention reason, so a blur-gate
    # abstention on a non-plant counts as a rejection here.
    print("recall of the row's own class | n | abstentions (scored wrong, not false fires)")
    print(f"{'class / arm / species':38s} {la:>9s} {lb:>9s} {'delta':>8s}   n   "
          f"{'abst_' + la[:3]:>6s} {'abst_' + lb[:3]:>6s}")
    for ci, cname in enumerate(names):
        m = yt == ci
        if not m.any():
            continue
        arm_values = sorted(set(ar[m]))
        pooled = len(arm_values) > 1
        _line(cname, m, ci, (cname, "*", "*"),
              "   <- POOLED; read the arms below, not this" if pooled else "")
        for arm in arm_values:
            a = m & (ar == arm)
            if arm or pooled:
                _line(f"  [{arm or 'plain'}]{'':30s}"[:38], a, ci, (cname, arm, "*"),
                      _ARM_NOTES.get(arm, ""))
            for s in sorted(set(unit[a])):
                _line(f"    └ {s:32s}"[:38], a & (unit == s), ci, (cname, arm, s))
        if ci == healthy_idx:
            _breakdown(cname, m, "healthy FALSE-FIRE (any disease call)",
                       disease_idx, "false_fire")
            _breakdown(cname, m, "healthy FALSE-REJECT (nap / abstain)",
                       reject_idx, "false_reject")
        if ci == nap_idx:
            _breakdown(cname, m, "not_a_plant ACCEPTED AS PLANT",
                       plant_idx, "accepted_as_plant")

    # Between-species spread is what decides whether a pooled number means
    # anything. With k=2 species at SD 0.616 the 95% CI on a domain-level scorch
    # estimate is +-0.854 — wider than the unit interval. Per arm, never across
    # arms, and only over rows that name a species (a non-plant has none).
    print()
    for ci, cname in enumerate(names):
        m = (yt == ci) & (species != "")
        arm_values = sorted(set(ar[m]))
        for arm in arm_values:
            a = m & (ar == arm)
            label = f"{cname}[{arm}]" if arm or len(arm_values) > 1 else cname
            per_sp = [float((ya[a & (species == s)] == ci).mean())
                      for s in sorted(set(species[a]))]
            k = len(per_sp)
            if k < 2:
                continue
            sd = float(np.std(per_sp, ddof=1))
            # Student's t, not 1.96. At k=2 the two differ by 6.5x, and a file whose
            # whole argument is "your interval is wider than you think" cannot
            # afford to understate its own by that much.
            crit = _T95.get(k - 1, 1.96)
            half = crit * sd / np.sqrt(k)
            verdict = ("OK for a domain claim" if half <= 0.15
                       else "TOO FEW SPECIES for a domain claim")
            print(f"{label:38s} k={k:2d} species, between-species SD={sd:.3f} "
                  f"-> 95% CI +-{half:.3f} (t_{k-1}={crit:.3f})  {verdict}")
            out_rows.append({"class": cname, "arm": arm, "unit": "*",
                             "stat": "between_species_half_width",
                             "a": float(half), "b": None, "n": k})
            if half > 0.15 and sd > 0:
                # Solved with z, since the k that satisfies this is large enough for
                # t -> z; quoting a t-based k here would be spurious precision.
                need = int(np.ceil((1.96 * sd / 0.15) ** 2))
                print(f"{'':38s} reaching +-0.15 needs k~{need} species at this SD")
    return {"family_verified": family_verified, "rows": out_rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path,
                    help="sources.json declaring the sources, layouts and arms (v3)")
    ap.add_argument("--src", type=Path,
                    help="legacy v2 source root (e.g. ext/flower_data); not with --config")
    ap.add_argument("--out", type=Path, default=Path("oodbench"))
    ap.add_argument("--per-class-cap", type=int, default=400)
    ap.add_argument("--min-clusters-per-class", type=int,
                    default=MIN_CLUSTERS_PER_CLASS)
    ap.add_argument(
        "--family-manifest", type=Path,
        help="exhaustive offline SIFT/RANSAC family CSV for a large --src; a "
             "--config source names its own")
    ap.add_argument("--classes-json", type=Path,
                    help="take the label taxonomy from a bundle's classes.json "
                         "(default: classifier.DEFAULT_CLASS_NAMES)")
    ap.add_argument("--notebook", type=Path, default=DEFAULT_NOTEBOOK,
                    help="training notebook whose deny-lists the sources are checked against")
    ap.add_argument("--publish-slug", default="",
                    help="Kaggle slug the result will be published under; must be in the "
                         "notebook's KAGGLE_BENCHMARK_SLUGS (overrides the config's)")
    ap.add_argument("--report", type=Path, help="preds.json to break down per species")
    ap.add_argument("--manifest", type=Path, help="benchmark manifest.csv (with --report)")
    args = ap.parse_args()
    if args.report:
        if not args.manifest:
            sys.exit("--report needs --manifest")
        report(args.report, args.manifest)
        return
    if bool(args.src) == bool(args.config):
        sys.exit("give exactly one of --config (v3) or --src (legacy), or use --report")
    if args.config:
        if args.family_manifest:
            sys.exit("--family-manifest goes with --src; a --config source names its own")
        sources, slug = load_config(args.config)
    else:
        if not args.src.is_dir():
            sys.exit(f"source directory does not exist: {args.src}")
        sources, slug = [legacy_source(args.src, args.family_manifest)], ""
    build_benchmark(
        sources, args.out, args.per_class_cap, args.min_clusters_per_class,
        class_names=load_class_names(args.classes_json),
        deny_lists=load_deny_lists(args.notebook),
        benchmark_slug=(args.publish_slug.strip().lower() or slug))


if __name__ == "__main__":
    main()
