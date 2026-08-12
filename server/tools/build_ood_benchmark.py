#!/usr/bin/env python3
"""Build a NEVER-TRAINED out-of-domain benchmark that passes this repo's own
eval gates, then hand it to ``tools/paired_eval.py``.

Why this exists
---------------
The first version of this benchmark (2026-08-11) caught a real 2.7.0 regression
and then failed the near-dup criterion `DATASET_LABELING_PLAN.md` requires of
*any* eval set: 2,000 files held **501 SHA-unique images and 206 pHash clusters**
at Hamming <= 6. Every count and p-value it produced was inflated ~4x, and one
finding (healthy false-fire) evaporated entirely once deduped. A benchmark that
is not deduped is not a benchmark — it is the same picture, counted repeatedly.

So this script does the gating in code rather than in a follow-up audit:

* **pHash dedup at Hamming <= 6**, the repo's stated criterion, keeping ONE
  representative per cluster. SHA dedup alone is not enough — augmented copies
  of a photo have different bytes and identical content.
* **Species is recorded and reported**, because the first benchmark's entire
  headline delta turned out to be 31 clusters of a single species (hibiscus),
  while the incumbent was *already* collapsed on the other one (banana_bush).
  A per-class number that is really a per-species number is a trap.
* **A minimum cluster count per class**, so the tree cannot silently ship a
  slice too thin to carry a release decision.

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
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

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
MIN_CLUSTERS_PER_CLASS = 60


def _phash(path: Path):
    import imagehash
    from PIL import Image
    try:
        with Image.open(path) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception:
        return None


def dedup(paths: list[Path], threshold: int = PHASH_MAX_HAMMING) -> list[Path]:
    """Keep one representative per pHash cluster.

    Greedy single-pass against kept representatives — the same shape as the
    notebook's ``_dedup_train_in_place``, so the benchmark is filtered by the
    criterion the training corpus is filtered by.
    """
    kept: list[Path] = []
    reps: list = []
    for p in sorted(paths):
        h = _phash(p)
        if h is None:
            continue
        if any((h - r) <= threshold for r in reps):
            continue
        reps.append(h)
        kept.append(p)
    return kept


def build(src: Path, out: Path, per_class_cap: int) -> None:
    # group: class -> species -> [paths]
    pool: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    for split_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        for species_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
            for cond_dir in sorted(p for p in species_dir.iterdir() if p.is_dir()):
                cls = CONDITION_MAP.get(cond_dir.name.lower())
                if not cls:
                    continue
                pool[cls][species_dir.name] += [
                    f for f in cond_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in IMG_EXTS
                ]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for cls, by_species in sorted(pool.items()):
        print(f"\n{cls}:")
        (out / cls).mkdir(parents=True, exist_ok=True)
        for species, files in sorted(by_species.items()):
            uniq = dedup(files)
            # Cap PER SPECIES so no single species can dominate a class the way
            # hibiscus dominated the first benchmark's scorch arm.
            take = uniq[: max(1, per_class_cap // max(len(by_species), 1))]
            for f in take:
                dst = out / cls / f"{species}__{f.stem}{f.suffix.lower()}"
                shutil.copy(f, dst)
                rows.append({"filename": f"{cls}/{dst.name}", "class": cls,
                             "species": species, "source": str(src)})
            print(f"  {species:28s} {len(files):5d} files -> {len(uniq):4d} clusters -> {len(take):4d} kept")

    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "class", "species", "source"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nwrote {out} — {len(rows)} images, manifest.csv with species")
    thin = {c: n for c, n in Counter(r["class"] for r in rows).items()
            if n < MIN_CLUSTERS_PER_CLASS}
    if thin:
        print(f"WARN: under {MIN_CLUSTERS_PER_CLASS} clusters — too thin to carry a "
              f"release decision on its own: {thin}")
    for cls in sorted(pool):
        n_sp = len({r["species"] for r in rows if r["class"] == cls})
        if n_sp < 3:
            print(f"WARN: {cls} spans only {n_sp} species — report PER SPECIES and do "
                  f"not read a class-level delta as a domain-level one.")


def report(preds_path: Path, manifest: Path) -> None:
    """Per-species paired breakdown of a preds.json produced by paired_eval."""
    import numpy as np

    d = json.loads(preds_path.read_text())
    if "items" not in d:
        sys.exit("preds.json has no `items` — re-run paired_eval (it records image paths now)")
    species = {r["filename"].split("/")[-1]: r["species"]
               for r in csv.DictReader(open(manifest, newline="", encoding="utf-8"))}
    names = d["class_names"]
    yt = np.array(d["y_true"])
    ya = np.array(d["y_pred_a"])
    yb = np.array(d.get("y_pred_b") or d["y_pred_a"])
    la, lb = d.get("label_a", "a"), d.get("label_b", "b")
    sp = np.array([species.get(Path(i).name, "?") for i in d["items"]])

    print(f"{'class / species':38s} {la:>9s} {lb:>9s} {'delta':>8s}   n")
    for ci, cname in enumerate(names):
        m = yt == ci
        if not m.any():
            continue
        print(f"{cname:38s} {(ya[m]==ci).mean():9.3f} {(yb[m]==ci).mean():9.3f} "
              f"{(yb[m]==ci).mean()-(ya[m]==ci).mean():+8.3f} {int(m.sum()):4d}")
        for s in sorted(set(sp[m])):
            k = m & (sp == s)
            print(f"    └ {s:32s} {(ya[k]==ci).mean():9.3f} {(yb[k]==ci).mean():9.3f} "
                  f"{(yb[k]==ci).mean()-(ya[k]==ci).mean():+8.3f} {int(k.sum()):4d}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="extracted source root (e.g. ext/flower_data)")
    ap.add_argument("--out", type=Path, default=Path("oodbench"))
    ap.add_argument("--per-class-cap", type=int, default=400)
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
    build(args.src, args.out, args.per_class_cap)


if __name__ == "__main__":
    main()
