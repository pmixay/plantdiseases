#!/usr/bin/env python3
"""Score two model bundles on the SAME images and emit ``preds.json`` for
``tools/eval_bootstrap.py`` — the paired A/B that decides ship / no-ship.

Why this exists
---------------
A floor ("is this model broken?") is not a comparison between candidates.
Historical release narratives in the research archive are not trusted as
proof, but they correctly expose the missing instrument: two immutable bundles
must predict on one common, honestly held-out set and uncertainty must resample
independent capture families.

The two traps this tool exists to avoid
---------------------------------------
1. **Train leakage into the comparison.** An image the incumbent trained on
   flatters the incumbent. Every non-supplement source (PlantDoc, iNat,
   PlantVillage, DoctorP…) was in *both* models' training corpora under
   *different* per-run splits, so a val image of the challenger may sit in the
   incumbent's train set. Only the eval-supplement carries per-row provenance
   that says which era a row entered in, so ``--provenance`` filters the eval
   set down to supplement rows the incumbent never saw. Non-supplement images
   are dropped whenever a provenance filter is given — silently keeping them
   is the leak.
2. **Comparing across corpora.** Mutable upstream snapshots and independently
   redrawn splits make unpaired point estimates incomparable. Pairing on one
   manifest-bound image list removes that source of variation, and the
   bootstrap applies one capture-family resample to both candidates.

Each bundle is scored through ``DiseaseClassifier`` — the SERVING code path —
so every bundle gets its own declared ``img_size``, ``eval_resize_ratio`` and
TTA mode, exactly as it would in production. Predictions are matched by CLASS
NAME, so two bundles with different class ORDER compare correctly.

The default scores the **full served pipeline**. ``--argmax`` is retained only
for a historical Stage-2 diagnostic; it must not decide a deployment swap.

Usage
-----
::

    # Grab the manifest that carries provenance (needs a Kaggle token):
    #   curl -sSL -H "Authorization: Bearer $KAGGLE_API_TOKEN" \\
    #     "https://www.kaggle.com/api/v1/datasets/download/maxayyt5/\\
    # houseplant-disease-eval-supplement?fileName=manifest.csv" -o manifest.csv

    python -m server.tools.paired_eval \\
        --bundle-a bundles/2.6.8   --label-a 2.6.8 \\
        --bundle-b bundles/2.7.0   --label-b 2.7.0 \\
        --eval-dir val/realworld \\
        --manifest manifest.csv \\
        --provenance all --eval-through-version 2.7.0 \\
        --served --out preds.json --run-bootstrap

``--eval-dir`` is an ImageFolder tree (``<class_name>/<image>``) — i.e. the
notebook's ``data_v2/classification/val/realworld`` copied off the training
box. Pass ``--provenance ''`` to score every image in the tree (useful for
reproducing a single run's own number; NOT valid for a cross-version A/B).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path, PurePosixPath

# Allow both ``python -m server.tools.paired_eval`` (repo root) and
# ``python tools/paired_eval.py`` (from server/) — mirrors export_data.py.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

# NOTE: ``DiseaseClassifier`` is imported lazily inside the functions that need
# it. The manifest join and the provenance filter are pure logic with no ONNX
# dependency, and they are the part most worth unit-testing — importing the
# runtime stack at module scope would make those tests need onnxruntime.

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ABSTAIN_SENTINEL = "__ABSTAIN__"
_SERVED_REJECTION_WARNINGS = {"nap_ood_gated", "blurry_image"}

# The eval-supplement's on-disk stem, built by the notebook's
# ``_save_archive_member``: ``sanitise(f"supp_{class/relative/path/stem}")``.
# Replicated here so a manifest row can be matched back to the file it became.
SUPPLEMENT_PREFIX = "supp_"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _legacy_bundle_entry(bundle_dir: Path) -> Path:
    """Resolve an old unbound bundle for an explicitly diagnostic run only."""
    for name in ("classifier_int8.onnx", "classifier.onnx"):
        path = bundle_dir / name
        if path.is_file():
            return path
    raise SystemExit(
        f"{bundle_dir}: no classifier_int8.onnx or classifier.onnx. Point at the "
        "unpacked bundle root (the directory holding classes.json)."
    )


def _load_bundle_binding(
    bundle_dir: Path,
    *,
    allow_legacy_unbound: bool = False,
) -> dict:
    """Validate one immutable bundle and return its exact classifier binding.

    New comparisons are release evidence, so they fail closed unless every
    manifest-listed byte verifies and the manifest selects exactly one graph.
    ``allow_legacy_unbound`` exists solely to reproduce historical diagnostics;
    its result is marked unbound in the output payload.
    """
    bundle_dir = Path(bundle_dir)
    if not bundle_dir.is_dir():
        raise SystemExit(f"--bundle {bundle_dir} is not a directory")
    manifest_path = bundle_dir / "bundle_manifest.json"
    if not manifest_path.is_file():
        if not allow_legacy_unbound:
            raise SystemExit(
                f"{bundle_dir}: bundle_manifest.json is required. This comparison "
                "cannot prove which graph/sidecars were served. Pass "
                "--allow-legacy-unbound only for a labelled historical diagnostic."
            )
        classifier = _legacy_bundle_entry(bundle_dir)
        if not (bundle_dir / "classes.json").is_file():
            raise SystemExit(
                f"{bundle_dir}: no classes.json — class order cannot be verified"
            )
        return {
            "bound": False,
            "classifier_path": classifier,
            "selected_classifier": classifier.name,
            "bundle_manifest_sha256": None,
            "serving_contract_sha256": None,
            "diagnostic_advisory": "legacy unbound bundle; not release evidence",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{manifest_path}: invalid bundle manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise SystemExit(f"{manifest_path}: unsupported manifest schema")
    selection = manifest.get("classifier_selection")
    artifacts = manifest.get("artifacts")
    contract = manifest.get("serving_contract")
    if not isinstance(selection, dict) or selection.get("fallback_allowed") is not False:
        raise SystemExit(f"{manifest_path}: classifier fallback must be explicitly false")
    if not isinstance(artifacts, dict) or not artifacts:
        raise SystemExit(f"{manifest_path}: artifacts must be a non-empty object")
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise SystemExit(f"{manifest_path}: serving_contract schema is missing/unsupported")
    selected = selection.get("path")
    classifier_spec = artifacts.get("classifier")
    if (not isinstance(selected, str) or not selected
            or not isinstance(classifier_spec, dict)
            or classifier_spec.get("path") != selected):
        raise SystemExit(f"{manifest_path}: classifier selection/role disagree")
    required_roles = {
        "classifier", "detector", "classes", "temperature", "conformal",
        "data_manifest", "run_manifest",
    }
    missing_roles = sorted(required_roles - set(artifacts))
    if missing_roles:
        raise SystemExit(f"{manifest_path}: missing required artifact roles {missing_roles}")
    expected_paths = {
        "detector": "detector.onnx", "classes": "classes.json",
        "temperature": "temperature.json", "conformal": "conformal.json",
        "data_manifest": "data_manifest.json", "run_manifest": "run_manifest.json",
    }
    bad_paths = {
        role: (artifacts.get(role) or {}).get("path")
        for role, wanted in expected_paths.items()
        if (artifacts.get(role) or {}).get("path") != wanted
    }
    if bad_paths:
        raise SystemExit(f"{manifest_path}: non-canonical required artifact paths {bad_paths}")
    verified: dict[str, Path] = {}
    seen_paths: set[str] = set()
    for role, spec in artifacts.items():
        if not isinstance(role, str) or not isinstance(spec, dict):
            raise SystemExit(f"{manifest_path}: invalid artifact entry {role!r}")
        raw = spec.get("path")
        rel = PurePosixPath(raw) if isinstance(raw, str) else None
        if (rel is None or rel.is_absolute() or not rel.parts or ".." in rel.parts
                or "\\" in raw or rel.as_posix() in seen_paths):
            raise SystemExit(f"{manifest_path}: unsafe/duplicate artifact path {raw!r}")
        seen_paths.add(rel.as_posix())
        expected_sha = spec.get("sha256")
        expected_size = spec.get("size_bytes")
        if (not isinstance(expected_sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
                or isinstance(expected_size, bool) or not isinstance(expected_size, int)
                or expected_size < 0):
            raise SystemExit(f"{manifest_path}: invalid digest/size for artifact {role!r}")
        path = bundle_dir.joinpath(*rel.parts)
        if not path.is_file():
            raise SystemExit(f"{manifest_path}: artifact missing: {raw}")
        if path.stat().st_size != expected_size or _sha256_file(path) != expected_sha:
            raise SystemExit(f"{manifest_path}: artifact digest/size mismatch: {raw}")
        verified[role] = path
    selected_rel = PurePosixPath(selected)
    if selected_rel.parent != PurePosixPath(".") or selected_rel.suffix != ".onnx":
        raise SystemExit(f"{manifest_path}: selected classifier must be one root ONNX file")
    root_classifiers = sorted(
        p.name for p in bundle_dir.glob("classifier*.onnx") if p.is_file()
    )
    if root_classifiers != [selected_rel.name]:
        raise SystemExit(
            f"{manifest_path}: expected exactly [{selected_rel.name!r}] at bundle root; "
            f"found {root_classifiers}"
        )
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or re.fullmatch(r"[0-9a-f]{64}", bundle_id) is None:
        raise SystemExit(f"{manifest_path}: bundle_id must be a SHA-256 hex digest")
    identity_payload = dict(manifest)
    identity_payload.pop("bundle_id", None)
    if _canonical_json_sha256(identity_payload) != bundle_id:
        raise SystemExit(f"{manifest_path}: bundle_id does not match canonical manifest identity")
    data_identity = manifest.get("data_manifest")
    if not isinstance(data_identity, dict):
        raise SystemExit(f"{manifest_path}: data_manifest identity is missing")
    if data_identity.get("manifest_sha256") != artifacts["data_manifest"].get("sha256"):
        raise SystemExit(f"{manifest_path}: data-manifest artifact/identity digest mismatch")
    try:
        data_doc = json.loads(verified["data_manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{verified['data_manifest']}: invalid data manifest: {exc}") from exc
    if data_identity.get("dataset_sha256") != data_doc.get("classification_sha256"):
        raise SystemExit(f"{manifest_path}: dataset identity disagrees with data manifest")
    if not (bundle_dir / "classes.json").exists():
        raise SystemExit(
            f"{bundle_dir}: no classes.json — without it the class ORDER is a guess "
            "and every prediction could be silently mislabelled."
        )
    return {
        "bound": True,
        "classifier_path": verified["classifier"],
        "selected_classifier": selected,
        "bundle_id": bundle_id,
        "run_id": manifest.get("run_id"),
        "model_version": manifest.get("model_version"),
        "data_manifest_sha256": data_identity.get("manifest_sha256"),
        "dataset_sha256": data_identity.get("dataset_sha256"),
        "bundle_manifest_sha256": _sha256_file(manifest_path),
        "serving_contract_sha256": _canonical_json_sha256(contract),
    }


def _bundle_entry(bundle_dir: Path, *, allow_legacy_unbound: bool = False) -> Path:
    """Return the exact manifest-selected classifier graph."""
    return _load_bundle_binding(
        bundle_dir, allow_legacy_unbound=allow_legacy_unbound,
    )["classifier_path"]


def _sanitise_stem(raw: str) -> str:
    """Exactly the notebook's on-disk stem sanitisation."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def _version_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if match is None:
        raise ValueError(f"expected semantic model version, got {value!r}")
    return tuple(map(int, match.groups()))


def _manifest_provenance(
    manifest: Path,
    split_hint: str = "",
    eval_through_version: str | None = None,
) -> dict[str, str]:
    """Map on-disk key -> provenance for every supplement row.

    Two key forms are emitted for each row, because the same manifest has to
    join against two different trees:

    * ``supp_<class>_<stem>`` — what the NOTEBOOK writes into the split dirs
      (``_save_archive_member`` prefixes to keep stems unique across archives).
    * ``<class>/<relative/path/stem>`` — the RAW Kaggle tree, when the
      supplement itself is used as the eval substrate because the training
      box's splits are gone.

    Raw stems are class-qualified: the prefix exists precisely because two class
    folders can both hold a ``1.jpg``, and an unqualified key would collide.

    ``split_hint`` (e.g. ``eval_ok``) additionally restricts to rows the
    notebook was willing to hold out. It matters when scoring the raw tree:
    ``train_only`` and ``boundary`` rows are 100% inside the challenger's
    training set, so keeping them would compare a model against its own
    training data.
    """
    import csv

    with open(manifest, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{manifest}: no rows")
    if "provenance" not in rows[0]:
        raise SystemExit(
            f"{manifest}: no `provenance` column — this build predates cross-version "
            "provenance, so an honest incumbent-never-saw-it filter is impossible. "
            "Publish a supplement build that stamps it, or pass --provenance '' and "
            "accept that the comparison is leaky (and say so in the write-up)."
        )
    if split_hint and "split_hint" not in rows[0]:
        raise SystemExit(f"{manifest}: --split-hint given but the manifest has no such column")
    through = None
    if eval_through_version is not None:
        try:
            through = _version_key(eval_through_version)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if "first_seen_model_version" not in rows[0]:
            raise SystemExit(
                f"{manifest}: no first_seen_model_version; candidate/incumbent-aware "
                "evaluation is impossible (publish dataset v9)")
    out: dict[str, str] = {}

    def add(key: str, prov: str, line_no: int) -> None:
        if key in out:
            raise SystemExit(
                f"{manifest}: manifest line {line_no} collides on evaluation key {key!r}")
        out[key] = prov

    for line_no, r in enumerate(rows, start=2):
        cls = (r.get("class") or "").strip()
        raw = (r.get("filename") or "").strip().replace("\\", "/")
        rel = PurePosixPath(raw)
        if (not cls or not raw or rel.is_absolute() or ".." in rel.parts
                or not rel.parts or rel.parts[0] != cls):
            raise SystemExit(
                f"{manifest}: line {line_no} has unsafe/disagreeing filename/class: "
                f"{raw!r}, {cls!r}")
        if split_hint and (r.get("split_hint") or "").strip() != split_hint:
            continue
        if "filter_verdict" in rows[0] and (r.get("filter_verdict") or "").strip() != "keep":
            continue
        if through is not None:
            first_seen = (r.get("first_seen_model_version") or "").strip().lower()
            if first_seen != "never":
                try:
                    if _version_key(first_seen) <= through:
                        continue
                except ValueError as exc:
                    raise SystemExit(f"{manifest}: line {line_no}: {exc}") from exc
        prov = (r.get("provenance") or "").strip()
        raw_no_suffix = rel.with_suffix("").as_posix()
        # Same identity transform as train_notebook.ipynb. Flat historical rows
        # remain supp_<class>_<stem>; nested rows include their full relative
        # parent, so same basenames cannot overwrite each other's provenance.
        disk_stem = _sanitise_stem(f"{SUPPLEMENT_PREFIX}{raw_no_suffix}")
        add(disk_stem, prov, line_no)
        add(raw_no_suffix, prov, line_no)
    return out


def _manifest_capture_families(
    manifest: Path, split_hint: str = "",
) -> dict[str, str] | None:
    """Map both notebook/raw keys to stable family IDs, or mark legacy advisory."""
    import csv

    with open(manifest, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows or "capture_family_id" not in rows[0]:
        return None
    out: dict[str, str] = {}
    for line_no, row in enumerate(rows, start=2):
        if split_hint and (row.get("split_hint") or "").strip() != split_hint:
            continue
        if "filter_verdict" in rows[0] and (row.get("filter_verdict") or "").strip() != "keep":
            continue
        cls = (row.get("class") or "").strip()
        raw = (row.get("filename") or "").strip().replace("\\", "/")
        rel = PurePosixPath(raw)
        family = (row.get("capture_family_id") or "").strip()
        if (not family or not cls or not raw or rel.is_absolute() or ".." in rel.parts
                or not rel.parts or rel.parts[0] != cls):
            raise SystemExit(
                f"{manifest}: line {line_no} lacks safe class/path/capture_family_id")
        raw_no_suffix = rel.with_suffix("").as_posix()
        for key in (_sanitise_stem(f"{SUPPLEMENT_PREFIX}{raw_no_suffix}"), raw_no_suffix):
            if key in out:
                raise SystemExit(
                    f"{manifest}: line {line_no} collides on family key {key!r}")
            out[key] = family
    return out


def collect_eval_items(
    eval_dir: Path, class_names: list[str],
    provenance_ok: set[str] | None, prov_by_stem: dict[str, str] | None,
    group_by_stem: dict[str, str] | None = None,
) -> tuple[list[Path], list[int], dict]:
    """Walk an ImageFolder tree -> (paths, y_true, stats).

    ``y_true`` indexes ``class_names``. When a provenance filter is active,
    only supplement rows whose provenance is in ``provenance_ok`` survive:
    everything else either has no provenance record (non-supplement sources,
    which both models trained on under different splits) or belongs to an era
    the incumbent trained on.
    """
    eval_dir = Path(eval_dir)
    if not eval_dir.is_dir():
        raise ValueError(f"eval_dir is not a directory: {eval_dir}")
    paths: list[Path] = []
    y_true: list[int] = []
    stats = {
        "seen": 0, "kept": 0, "dropped_unknown_class": 0,
        "dropped_not_supplement": 0, "dropped_provenance": 0,
        "kept_by_provenance": Counter(), "kept_by_class": Counter(),
        "group_ids": [],
    }
    name_to_idx = {n: i for i, n in enumerate(class_names)}
    for cls_dir in sorted(p for p in eval_dir.iterdir() if p.is_dir()):
        idx = name_to_idx.get(cls_dir.name)
        for img in sorted(cls_dir.rglob("*")):
            if not img.is_file() or img.suffix.lower() not in IMG_EXTS:
                continue
            stats["seen"] += 1
            if idx is None:
                stats["dropped_unknown_class"] += 1
                continue
            if provenance_ok is not None:
                lookup = prov_by_stem or {}
                prov = lookup.get(img.stem)
                if prov is None:      # raw supplement tree: full class/path key
                    prov = lookup.get(img.relative_to(eval_dir).with_suffix("").as_posix())
                if prov is None:
                    stats["dropped_not_supplement"] += 1
                    continue
                # ``{"all"}`` keeps every supplement row regardless of era while
                # still dropping non-supplement sources. That is the CONSERVATIVE
                # test: recycled rows sit inside the incumbent's training set, so
                # the slice is biased TOWARD the incumbent, and a challenger that
                # wins there has won against a stacked deck. It is also the only
                # slice thick enough to move the weak classes.
                if "all" not in provenance_ok and prov not in provenance_ok:
                    stats["dropped_provenance"] += 1
                    continue
                stats["kept_by_provenance"][prov] += 1
            paths.append(img)
            y_true.append(idx)
            group = None
            if group_by_stem is not None:
                group = group_by_stem.get(img.stem)
                if group is None:
                    group = group_by_stem.get(
                        img.relative_to(eval_dir).with_suffix("").as_posix())
            stats["group_ids"].append(group)
            stats["kept"] += 1
            stats["kept_by_class"][cls_dir.name] += 1
    return paths, y_true, stats


def _shared_class_names(names_a: list[str], names_b: list[str],
                        allow_mismatch: bool = False) -> list[str]:
    """Validate class contracts and return A-ordered shared names."""
    if len(names_a) != len(set(names_a)) or len(names_b) != len(set(names_b)):
        raise ValueError("bundle class lists contain duplicate names")
    if set(names_a) != set(names_b) and not allow_mismatch:
        raise ValueError(
            "bundle class sets differ; cross-taxonomy scoring is ambiguous. "
            "Use --allow-class-mismatch only for an explicit diagnostic.")
    return [n for n in names_a if n in set(names_b)]


def _stratified_limit(paths: list[Path], y_true: list[int], limit: int):
    """Deterministic class-round-robin smoke slice, never a class-sorted prefix."""
    if limit <= 0 or limit >= len(paths):
        return paths, y_true
    buckets: dict[int, list[int]] = {}
    for i, y in enumerate(y_true):
        buckets.setdefault(y, []).append(i)
    cursors = {y: 0 for y in buckets}
    chosen = []
    while len(chosen) < limit:
        advanced = False
        for y in sorted(buckets):
            cur = cursors[y]
            if cur < len(buckets[y]):
                chosen.append(buckets[y][cur])
                cursors[y] += 1
                advanced = True
                if len(chosen) >= limit:
                    break
        if not advanced:
            break
    return [paths[i] for i in chosen], [y_true[i] for i in chosen]


def _served_class_or_abstain(result: dict) -> str:
    """Map production rejection warnings to an explicit scored-wrong sentinel."""
    warnings = result.get("warnings") or []
    codes = set()
    for warning in warnings:
        if isinstance(warning, str):
            codes.add(warning)
        elif isinstance(warning, dict):
            codes.add(str(warning.get("code") or warning.get("warning") or ""))
    codes.update(str(x) for x in (result.get("warning_codes") or []))
    if codes & _SERVED_REJECTION_WARNINGS:
        return ABSTAIN_SENTINEL
    return result.get("class_name") or result.get("disease") or "not_a_plant"


def predict_served(
    bundle: Path,
    paths: list[Path],
    label: str,
    classifier_path: Path | None = None,
) -> list[str]:
    """Predicted class names through the FULL production pipeline.

    Stage-2 argmax is what the notebook's ``[final val_realworld]`` reports, so
    it is the number that compares to history — but it is NOT what a user gets.
    The served path adds the Stage-1 detector crop, mean-voting across crops,
    the blur gate and the OOD AND-gate. Rejected inputs are explicit abstentions
    scored wrong (and reported as coverage), never their preserved raw argmax.
    A classifier-only benchmark can therefore rank different behavior from the
    detector/crop/quality/abstention path a user actually receives.
    """
    from PIL import Image

    from pipeline import PlantDiseasePipeline

    entry = classifier_path or _bundle_entry(bundle)
    pipe = PlantDiseasePipeline(
        bundle / "detector.onnx", entry,
        require_bundle_manifest=(bundle / "bundle_manifest.json").is_file(),
    )
    print(f"  [{label}] SERVED: detector + classifier + blur/OOD gates + abstention")
    preds: list[str] = []
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        with Image.open(p) as im:
            r = pipe.analyze(im.convert("RGB"))
        preds.append(_served_class_or_abstain(r))
        if i % 50 == 0 or i == len(paths):
            print(f"  [{label}] {i}/{len(paths)}  ({i/max(time.time()-t0,1e-6):.1f} img/s)",
                  flush=True)
    return preds


def predict(
    bundle: Path,
    paths: list[Path],
    batch: int,
    label: str,
    classifier_path: Path | None = None,
) -> list[str]:
    """Predicted CLASS NAMES for ``paths`` using one bundle's serving path."""
    from PIL import Image

    from classifier import DiseaseClassifier

    clf = DiseaseClassifier(
        classifier_path or _bundle_entry(bundle), exact_model_path=True,
    )
    print(
        f"  [{label}] classes={len(clf.CLASS_NAMES)} img_size={clf.img_size} "
        f"resize_ratio={clf.eval_resize_ratio} tta={clf.tta} arch={clf.architecture}"
    )
    preds: list[str] = []
    t0 = time.time()
    for i in range(0, len(paths), batch):
        chunk = paths[i:i + batch]
        imgs = [Image.open(p).convert("RGB") for p in chunk]
        preds.extend(r["class_name"] for r in clf.classify_batch(imgs))
        for im in imgs:
            im.close()
        done = min(i + batch, len(paths))
        if done % (batch * 10) == 0 or done == len(paths):
            rate = done / max(time.time() - t0, 1e-6)
            print(f"  [{label}] {done}/{len(paths)}  ({rate:.1f} img/s)", flush=True)
    return preds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle-a", type=Path, required=True, help="incumbent bundle dir")
    ap.add_argument("--bundle-b", type=Path, help="challenger bundle dir (omit to score one)")
    ap.add_argument("--label-a", default="model_a")
    ap.add_argument("--label-b", default="model_b")
    ap.add_argument("--eval-dir", type=Path, required=True, help="ImageFolder <class>/<img>")
    ap.add_argument("--manifest", type=Path, help="supplement manifest.csv (provenance join)")
    ap.add_argument(
        "--split-hint", default="",
        help="restrict to manifest rows with this split_hint (use `eval_ok` when the "
             "eval tree is the RAW supplement: train_only/boundary rows are 100%% "
             "inside the challenger's training set)",
    )
    ap.add_argument(
        "--provenance",
        default="all",
        help="comma-separated legacy provenance values to KEEP after the required "
             "first_seen_model_version filter. Default = all still-unseen rows. "
             "`all` keeps every supplement row and still drops non-supplement "
             "sources — biased TOWARD the incumbent, so a challenger that wins "
             "there wins against a stacked deck; run BOTH. Empty string disables "
             "the filter entirely and scores every image, which leaks in an "
             "unknown direction and is not valid for a cross-version A/B.",
    )
    ap.add_argument(
        "--eval-through-version",
        help="newest candidate/incumbent model version; rows first seen by this or "
             "an earlier model are excluded (required with --provenance)")
    ap.add_argument("--batch", type=int, default=16)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--served", dest="served", action="store_true", default=True,
                      help="score the full production pipeline (default)")
    mode.add_argument("--argmax", dest="served", action="store_false",
                      help="diagnostic Stage-2 whole-image argmax; not a ship comparison")
    ap.add_argument("--allow-class-mismatch", action="store_true",
                    help="diagnostic only: score the intersection when taxonomies differ")
    ap.add_argument(
        "--allow-legacy-unbound", action="store_true",
        help="diagnostic only: permit bundles without bundle_manifest.json; output is "
             "marked unbound and is not release evidence",
    )
    ap.add_argument("--limit", type=int, default=0, help="smoke-test on the first N images")
    ap.add_argument("--out", type=Path, default=Path("preds.json"))
    ap.add_argument("--run-bootstrap", action="store_true",
                    help="run tools/eval_bootstrap.py on the result")
    args = ap.parse_args()
    if args.limit < 0 or args.batch <= 0:
        raise SystemExit("--limit must be non-negative and --batch must be positive")
    if args.limit and args.run_bootstrap:
        raise SystemExit("--limit is a smoke test; do not report bootstrap CIs from it")

    prov_filter = {p.strip() for p in args.provenance.split(",") if p.strip()}
    prov_by_stem = None
    if prov_filter:
        if not args.manifest:
            raise SystemExit(
                "--provenance needs --manifest (the provenance lives in the "
                "supplement's manifest.csv). Pass --provenance '' to skip the filter."
            )
        if not args.eval_through_version:
            raise SystemExit(
                "--provenance requires --eval-through-version; a permanent eval_ok/"
                "provenance bit cannot say whether either compared model saw a row")
        prov_by_stem = _manifest_provenance(
            args.manifest, args.split_hint, args.eval_through_version)
        print(f"manifest: {len(prov_by_stem)} supplement rows with provenance")
        unknown = prov_filter - set(prov_by_stem.values()) - {"all"}
        if unknown:
            print(f"WARN: --provenance values not present in the manifest: {sorted(unknown)}")
    group_by_stem = (
        _manifest_capture_families(args.manifest, args.split_hint)
        if args.manifest else None)
    if args.manifest and group_by_stem is None:
        print("WARN: manifest has no capture_family_id; bootstrap CIs will be row-level "
              "advisories and cannot control pseudo-replication")

    binding_a = _load_bundle_binding(
        args.bundle_a, allow_legacy_unbound=args.allow_legacy_unbound)
    binding_b = (_load_bundle_binding(
        args.bundle_b, allow_legacy_unbound=args.allow_legacy_unbound)
        if args.bundle_b else None)
    if not binding_a["bound"] or (binding_b is not None and not binding_b["bound"]):
        print("WARN: legacy unbound bundle allowed for a diagnostic; do not use this "
              "result for a deployment decision")

    # Class space: the intersection, matched by NAME so class ORDER may differ.
    from classifier import DiseaseClassifier

    clf_a = DiseaseClassifier(binding_a["classifier_path"], exact_model_path=True)
    names_a = list(clf_a.CLASS_NAMES)
    if args.bundle_b:
        names_b = list(DiseaseClassifier(
            binding_b["classifier_path"], exact_model_path=True).CLASS_NAMES)
        try:
            common = _shared_class_names(names_a, names_b, args.allow_class_mismatch)
        except ValueError as e:
            raise SystemExit(str(e)) from e
        if set(names_a) != set(names_b):
            print(f"NOTE: class sets differ. only={sorted(set(names_a) ^ set(names_b))}; "
                  f"scoring the {len(common)} shared classes")
    else:
        common = names_a
    del clf_a

    paths, y_true, stats = collect_eval_items(
        args.eval_dir, common, prov_filter or None, prov_by_stem, group_by_stem,
    )
    if args.limit:
        paths, y_true = _stratified_limit(paths, y_true, args.limit)
        stats["group_ids"] = []
        stats["kept"] = len(paths)
        stats["kept_by_class"] = Counter(common[y] for y in y_true)
    print(f"\neval set: {len(paths)} images of {stats['seen']} seen")
    print(f"  dropped: not-supplement={stats['dropped_not_supplement']} "
          f"provenance={stats['dropped_provenance']} "
          f"unknown-class={stats['dropped_unknown_class']}")
    if stats["kept_by_provenance"]:
        print(f"  by provenance: {dict(stats['kept_by_provenance'])}")
    print(f"  by class: {dict(stats['kept_by_class'])}")
    if not paths:
        raise SystemExit("no images survived the filter — check --eval-dir and --provenance")
    thin = {c: stats["kept_by_class"].get(c, 0) for c in common
            if stats["kept_by_class"].get(c, 0) < 30}
    if thin:
        print(f"  WARN: classes under 30 images have paired recall CIs wider than "
              f"±0.14 and cannot settle anything on their own: {thin}")

    # Record the image list. Without it a preds.json cannot be broken down per
    # species/group afterwards — and the first out-of-domain benchmark's entire
    # headline delta turned out to be one species, which only became visible by
    # re-joining predictions to paths. Relative to --eval-dir so the file stays
    # portable between machines.
    def _binding_payload(binding: dict) -> dict:
        return {k: v for k, v in binding.items()
                if k != "classifier_path"}

    payload: dict = {"class_names": common, "y_true": y_true,
                     "items": [p.relative_to(args.eval_dir).as_posix() for p in paths],
                     "label_a": args.label_a,
                     "bundle_a": _binding_payload(binding_a)}
    if binding_b is not None:
        payload["bundle_b"] = _binding_payload(binding_b)
    if len(stats["group_ids"]) == len(paths) and all(stats["group_ids"]):
        payload["group_ids"] = stats["group_ids"]
    else:
        payload["independence_advisory"] = (
            "capture-family IDs absent/incomplete; row bootstrap is not release-grade")
    name_to_idx = {n: i for i, n in enumerate(common)}

    def _to_idx(preds: list[str], label: str) -> list[int]:
        out, off = [], 0
        for p in preds:
            i = name_to_idx.get(p)
            if i is None:      # predicted a class outside the shared space
                off += 1
                i = -1
            out.append(i)
        if off:
            print(f"  WARN: [{label}] predicted a non-shared class {off}x (scored as -1, "
                  f"i.e. always wrong — this is honest, not a bug)")
        return out

    def _score(bundle: Path, label: str, binding: dict) -> list[str]:
        if args.served:
            return predict_served(bundle, paths, label, binding["classifier_path"])
        return predict(bundle, paths, args.batch, label, binding["classifier_path"])
    payload["mode"] = "served" if args.served else "argmax"
    print(f"\nscoring {args.label_a}… (mode={payload['mode']})")
    _pred_a = _score(args.bundle_a, args.label_a, binding_a)
    _abstain_a = sum(p == ABSTAIN_SENTINEL for p in _pred_a)
    payload["abstention_a"] = {
        "count": _abstain_a, "rate": _abstain_a / len(paths),
        "coverage": 1.0 - _abstain_a / len(paths)}
    payload["y_pred_a"] = _to_idx(_pred_a, args.label_a)
    if args.bundle_b:
        print(f"\nscoring {args.label_b}…")
        payload["label_b"] = args.label_b
        _pred_b = _score(args.bundle_b, args.label_b, binding_b)
        _abstain_b = sum(p == ABSTAIN_SENTINEL for p in _pred_b)
        payload["abstention_b"] = {
            "count": _abstain_b, "rate": _abstain_b / len(paths),
            "coverage": 1.0 - _abstain_b / len(paths)}
        payload["y_pred_b"] = _to_idx(_pred_b, args.label_b)
    print(f"  abstention [{args.label_a}]: {payload['abstention_a']}")
    if args.bundle_b:
        print(f"  abstention [{args.label_b}]: {payload['abstention_b']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwrote {args.out}  ({len(paths)} paired predictions)")

    if args.run_bootstrap:
        print("\n" + "=" * 60)
        from tools.eval_bootstrap import main as boot_main  # noqa: PLC0415
        sys.argv = ["eval_bootstrap", str(args.out)]
        boot_main()


if __name__ == "__main__":
    main()
