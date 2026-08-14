#!/usr/bin/env python3
"""Score two model bundles on the SAME images and emit ``preds.json`` for
``tools/eval_bootstrap.py`` — the paired A/B that decides ship / no-ship.

Why this exists
---------------
The ship gate is a floor ("is this model broken?"), not a comparison. The one
time this repo shipped on the gate alone, **2.6.9 passed it and then lost the
paired bootstrap against 2.6.8** and had to be reverted. Every version swap
since is supposed to clear a paired test first, but nothing automated the
awkward part: getting two bundles to predict on one common, honestly-held-out
set of images.

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
2. **Comparing across corpora.** §2's upstream fetches are not deterministic
   (two 2.7.0 runs differed by ~0.02 realworld macro-F1 on their own splits),
   so unpaired point estimates from two runs are not comparable. Pairing on
   images removes that entirely: both models see the identical list, and the
   bootstrap resamples one index vector applied to both.

Each bundle is scored through ``DiseaseClassifier`` — the SERVING code path —
so every bundle gets its own declared ``img_size``, ``eval_resize_ratio`` and
TTA mode, exactly as it would in production. Predictions are matched by CLASS
NAME, so two bundles with different class ORDER compare correctly.

This scores **Stage 2 on the whole image**, which is what the notebook's
``[final val_realworld]`` number measures and therefore what the historical
figures are comparable to. It is deliberately not the detector-crop path.

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
        --provenance carried_from_v7,carried_from_v8,carried_from_v8b,new \\
        --out preds.json --run-bootstrap

``--eval-dir`` is an ImageFolder tree (``<class_name>/<image>``) — i.e. the
notebook's ``data_v2/classification/val/realworld`` copied off the training
box. Pass ``--provenance ''`` to score every image in the tree (useful for
reproducing a single run's own number; NOT valid for a cross-version A/B).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

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

# The eval-supplement's on-disk stem, built by the notebook's
# ``_save_archive_member``: ``sanitise(f"supp_{class}_{original stem}")``.
# Replicated here so a manifest row can be matched back to the file it became.
SUPPLEMENT_PREFIX = "supp_"


def _bundle_entry(bundle_dir: Path) -> Path:
    """DiseaseClassifier keys off ``model_path.parent``, not a directory.

    It expects a path to a file INSIDE the bundle and resolves
    ``classifier_int8.onnx`` (preferred) or ``classifier.onnx`` next to it, so
    the CLI takes the natural thing (a directory) and hands the loader the
    sentinel it wants. Validated here so a wrong --bundle path fails with a
    readable message instead of an AttributeError three frames down.
    """
    bundle_dir = Path(bundle_dir)
    if not bundle_dir.is_dir():
        raise SystemExit(f"--bundle {bundle_dir} is not a directory")
    if not any((bundle_dir / n).exists()
               for n in ("classifier_int8.onnx", "classifier.onnx")):
        raise SystemExit(
            f"{bundle_dir}: no classifier_int8.onnx or classifier.onnx. Point at the "
            "unpacked bundle root (the directory holding classes.json)."
        )
    if not (bundle_dir / "classes.json").exists():
        raise SystemExit(
            f"{bundle_dir}: no classes.json — without it the class ORDER is a guess "
            "and every prediction could be silently mislabelled."
        )
    return bundle_dir / "classifier.onnx"


def _sanitise_stem(raw: str) -> str:
    """Exactly the notebook's on-disk stem sanitisation."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def _manifest_provenance(manifest: Path, split_hint: str = "") -> dict[str, str]:
    """Map on-disk key -> provenance for every supplement row.

    Two key forms are emitted for each row, because the same manifest has to
    join against two different trees:

    * ``supp_<class>_<stem>`` — what the NOTEBOOK writes into the split dirs
      (``_save_archive_member`` prefixes to keep stems unique across archives).
    * ``<class>/<stem>`` — the RAW Kaggle tree, when the supplement itself is
      used as the eval substrate because the training box's splits are gone.

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
    out: dict[str, str] = {}
    for r in rows:
        cls = (r.get("class") or "").strip()
        stem = Path((r.get("filename") or "").strip()).stem
        if not cls or not stem:
            continue
        if split_hint and (r.get("split_hint") or "").strip() != split_hint:
            continue
        prov = (r.get("provenance") or "").strip()
        out[_sanitise_stem(f"{SUPPLEMENT_PREFIX}{cls}_{stem}")] = prov
        out[f"{cls}/{stem}"] = prov
    return out


def collect_eval_items(
    eval_dir: Path, class_names: list[str],
    provenance_ok: set[str] | None, prov_by_stem: dict[str, str] | None,
) -> tuple[list[Path], list[int], dict]:
    """Walk an ImageFolder tree -> (paths, y_true, stats).

    ``y_true`` indexes ``class_names``. When a provenance filter is active,
    only supplement rows whose provenance is in ``provenance_ok`` survive:
    everything else either has no provenance record (non-supplement sources,
    which both models trained on under different splits) or belongs to an era
    the incumbent trained on.
    """
    paths: list[Path] = []
    y_true: list[int] = []
    stats = {
        "seen": 0, "kept": 0, "dropped_unknown_class": 0,
        "dropped_not_supplement": 0, "dropped_provenance": 0,
        "kept_by_provenance": Counter(), "kept_by_class": Counter(),
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
                if prov is None:      # raw supplement tree: class-qualified key
                    prov = lookup.get(f"{cls_dir.name}/{img.stem}")
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
            stats["kept"] += 1
            stats["kept_by_class"][cls_dir.name] += 1
    return paths, y_true, stats


def predict_served(bundle: Path, paths: list[Path], label: str) -> list[str]:
    """Predicted class names through the FULL production pipeline.

    Stage-2 argmax is what the notebook's ``[final val_realworld]`` reports, so
    it is the number that compares to history — but it is NOT what a user gets.
    The served path adds the Stage-1 detector crop, mean-voting across crops,
    the blur gate, the OOD AND-gate and the weak-healthy reroute, and those
    move out-of-domain recall by ~20pp on their own: measured here, the gate
    alone destroys a third of the INCUMBENT's argmax-correct scorch calls. A
    benchmark scored only at argmax can therefore rank two bundles correctly
    and still misstate the size of the gap a user would feel.
    """
    from PIL import Image

    from pipeline import PlantDiseasePipeline

    pipe = PlantDiseasePipeline(bundle / "detector.onnx", _bundle_entry(bundle))
    print(f"  [{label}] SERVED: detector + classifier + blur/OOD gates + reroute")
    preds: list[str] = []
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        with Image.open(p) as im:
            r = pipe.analyze(im.convert("RGB"))
        preds.append(r.get("class_name") or r.get("disease") or "not_a_plant")
        if i % 50 == 0 or i == len(paths):
            print(f"  [{label}] {i}/{len(paths)}  ({i/max(time.time()-t0,1e-6):.1f} img/s)",
                  flush=True)
    return preds


def predict(bundle: Path, paths: list[Path], batch: int, label: str) -> list[str]:
    """Predicted CLASS NAMES for ``paths`` using one bundle's serving path."""
    from PIL import Image

    from classifier import DiseaseClassifier

    clf = DiseaseClassifier(_bundle_entry(bundle))
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
        default="carried_from_v7,carried_from_v8,carried_from_v8b,new",
        help="comma-separated provenance values to KEEP. Default = the strict "
             "slice the incumbent never trained on (UNBIASED, but thin). "
             "`all` keeps every supplement row and still drops non-supplement "
             "sources — biased TOWARD the incumbent, so a challenger that wins "
             "there wins against a stacked deck; run BOTH. Empty string disables "
             "the filter entirely and scores every image, which leaks in an "
             "unknown direction and is not valid for a cross-version A/B.",
    )
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--served", action="store_true",
                    help="score through the FULL pipeline (detector crop, blur/OOD gates, "
                         "reroute) instead of Stage-2 argmax. Slower, and the only mode "
                         "that reflects what a user actually gets.")
    ap.add_argument("--limit", type=int, default=0, help="smoke-test on the first N images")
    ap.add_argument("--out", type=Path, default=Path("preds.json"))
    ap.add_argument("--run-bootstrap", action="store_true",
                    help="run tools/eval_bootstrap.py on the result")
    args = ap.parse_args()

    prov_filter = {p.strip() for p in args.provenance.split(",") if p.strip()}
    prov_by_stem = None
    if prov_filter:
        if not args.manifest:
            raise SystemExit(
                "--provenance needs --manifest (the provenance lives in the "
                "supplement's manifest.csv). Pass --provenance '' to skip the filter."
            )
        prov_by_stem = _manifest_provenance(args.manifest, args.split_hint)
        print(f"manifest: {len(prov_by_stem)} supplement rows with provenance")
        unknown = prov_filter - set(prov_by_stem.values()) - {"all"}
        if unknown:
            print(f"WARN: --provenance values not present in the manifest: {sorted(unknown)}")

    # Class space: the intersection, matched by NAME so class ORDER may differ.
    from classifier import DiseaseClassifier

    clf_a = DiseaseClassifier(_bundle_entry(args.bundle_a))
    names_a = list(clf_a.CLASS_NAMES)
    if args.bundle_b:
        names_b = list(DiseaseClassifier(_bundle_entry(args.bundle_b)).CLASS_NAMES)
        common = [n for n in names_a if n in set(names_b)]
        if set(names_a) != set(names_b):
            print(f"NOTE: class sets differ. only={sorted(set(names_a) ^ set(names_b))}; "
                  f"scoring the {len(common)} shared classes")
    else:
        common = names_a
    del clf_a

    paths, y_true, stats = collect_eval_items(
        args.eval_dir, common, prov_filter or None, prov_by_stem,
    )
    if args.limit:
        paths, y_true = paths[:args.limit], y_true[:args.limit]
    print(f"\neval set: {len(paths)} images of {stats['seen']} seen")
    print(f"  dropped: not-supplement={stats['dropped_not_supplement']} "
          f"provenance={stats['dropped_provenance']} "
          f"unknown-class={stats['dropped_unknown_class']}")
    if stats["kept_by_provenance"]:
        print(f"  by provenance: {dict(stats['kept_by_provenance'])}")
    print(f"  by class: {dict(stats['kept_by_class'])}")
    if not paths:
        raise SystemExit("no images survived the filter — check --eval-dir and --provenance")
    thin = {c: n for c, n in stats["kept_by_class"].items() if n < 30}
    if thin:
        print(f"  WARN: classes under 30 images have paired recall CIs wider than "
              f"±0.14 and cannot settle anything on their own: {thin}")

    # Record the image list. Without it a preds.json cannot be broken down per
    # species/group afterwards — and the first out-of-domain benchmark's entire
    # headline delta turned out to be one species, which only became visible by
    # re-joining predictions to paths. Relative to --eval-dir so the file stays
    # portable between machines.
    payload: dict = {"class_names": common, "y_true": y_true,
                     "items": [str(p.relative_to(args.eval_dir)) for p in paths],
                     "label_a": args.label_a}
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

    _score = ((lambda b, lab: predict_served(b, paths, lab)) if args.served
              else (lambda b, lab: predict(b, paths, args.batch, lab)))
    payload["mode"] = "served" if args.served else "argmax"
    print(f"\nscoring {args.label_a}… (mode={payload['mode']})")
    payload["y_pred_a"] = _to_idx(_score(args.bundle_a, args.label_a), args.label_a)
    if args.bundle_b:
        print(f"\nscoring {args.label_b}…")
        payload["label_b"] = args.label_b
        payload["y_pred_b"] = _to_idx(_score(args.bundle_b, args.label_b), args.label_b)

    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwrote {args.out}  ({len(paths)} paired predictions)")

    if args.run_bootstrap:
        print("\n" + "=" * 60)
        from tools.eval_bootstrap import main as boot_main  # noqa: PLC0415
        sys.argv = ["eval_bootstrap", str(args.out)]
        boot_main()


if __name__ == "__main__":
    main()
