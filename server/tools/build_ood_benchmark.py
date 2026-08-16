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

# ── Taxonomy arms ────────────────────────────────────────────────────
# A class-level number pooled over species is a lie when the species disagree,
# and here they disagree completely: on the deployed 2.6.8 bundle + the NAP head
# veto, benchmark leaf_scorch is **31/31 on hibiscus and 4/31 on banana_bush**.
# Pooling those gives "0.565", a figure that describes neither.
#
# The reason is that the two species are not showing the same condition:
# banana_bush's "scorch" folder is turgor loss (wilting, foliage still green)
# while hibiscus's is necrosis (dead brown tissue). Our
# `leaf_scorch_underwatering` class is trained on the latter, so a model
# scoring 0 on banana_bush is not obviously wrong — it is being asked a
# different question. Every model in the archive behaves this way, which is the
# evidence for the split: across the 13 archived bundles scored served, hibiscus
# recall is >= banana_bush in ALL of them and strictly greater in 11 (the two
# ties are the collapsed 2.7.x bundles, both at 0.000). Medians: hibiscus 0.806
# vs banana_bush 0.129; best-ever 0.839 vs 0.419.
#
# So the arms are reported separately and read differently:
#   necrosis  — the release-blocking arm. 2.6.8 + veto is saturated at 31/31,
#               so it can only ever measure a REGRESSION now.
#   turgor    — an off-morphology probe. A model that scores 0 here is not
#               obviously wrong; treat movement as information, not as a gate.
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
    "banana_bush": "turgor",      # green wilting; 2.6.8+veto scores 4/31 here
}
DEFAULT_ARM = "unassigned"

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
        from PIL import Image
    except ImportError:
        return None
    import colorsys
    try:
        with Image.open(path) as im:
            im = im.convert("RGB").resize((160, 160))
            px = list(im.getdata())
    except Exception:
        return None
    subject = brown = 0
    for r, g, b in px:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if v > 0.90 and s < 0.15:        # paper / blown-out background
            continue
        subject += 1
        if not (60 / 360 <= h <= 170 / 360) and v < 0.75:
            brown += 1
    return (brown / subject) if subject else None


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
            browns = []
            # Where this species' rows START. `rows[-len(take):]` looks
            # equivalent and is not: at len(take)==0 it is `rows[-0:]` == the
            # WHOLE list, and the arm assignment below would then overwrite
            # every previously-written row with this species' arm. Reachable
            # whenever a mapped condition dir yields no usable images.
            start = len(rows)
            for f in take:
                dst = out / cls / f"{species}__{f.stem}{f.suffix.lower()}"
                shutil.copy(f, dst)
                bf = brown_fraction(dst)
                if bf is not None:
                    browns.append(bf)
                rows.append({"filename": f"{cls}/{dst.name}", "class": cls,
                             "species": species, "source": str(src),
                             "brown_fraction": "" if bf is None else f"{bf:.4f}"})
            med = sorted(browns)[len(browns) // 2] if browns else None
            # The arm describes WHICH CONDITION a species' diseased images show,
            # so it is meaningless on the healthy arm — a healthy hibiscus leaf
            # is not "necrosis". Left blank there rather than mislabelled.
            arm = SPECIES_ARMS.get(species, DEFAULT_ARM) if cls != "healthy" else ""
            for r in rows[start:]:
                r["arm"] = arm
            print(f"  {species:28s} {len(files):5d} files -> {len(uniq):4d} clusters -> "
                  f"{len(take):4d} kept   brown_median="
                  f"{'n/a' if med is None else f'{med:.3f}'}  arm={arm}")
            if arm == DEFAULT_ARM and cls != "healthy":
                print(f"    WARN: {species!r} is not in SPECIES_ARMS. Look at its images and "
                      f"decide whether they show necrosis or turgor loss, then add it — "
                      f"an unassigned arm is excluded from the release-blocking read.")

    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "class", "species", "source",
                                          "brown_fraction", "arm"])
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

    print(f"{'class / arm / species':38s} {la:>9s} {lb:>9s} {'delta':>8s}   n")
    for ci, cname in enumerate(names):
        m = yt == ci
        if not m.any():
            continue
        armed = sorted(a for a in set(ar[m]) if a)
        print(f"{cname:38s} {(ya[m]==ci).mean():9.3f} {(yb[m]==ci).mean():9.3f} "
              f"{(yb[m]==ci).mean()-(ya[m]==ci).mean():+8.3f} {int(m.sum()):4d}"
              + ("   <- POOLED; read the arms below, not this" if len(armed) > 1 else ""))
        if not armed:                      # no arms on this class (e.g. healthy)
            for s in sorted(set(sp[m])):
                k = m & (sp == s)
                print(f"    └ {s:32s} {(ya[k]==ci).mean():9.3f} {(yb[k]==ci).mean():9.3f} "
                      f"{(yb[k]==ci).mean()-(ya[k]==ci).mean():+8.3f} {int(k.sum()):4d}")
            continue
        for arm in armed:
            a = m & (ar == arm)
            note = {"necrosis": "  (release-blocking: regression metric)",
                    "turgor": "  (off-morphology probe: information, not a gate)"}.get(arm, "")
            print(f"  [{arm}]{'':30s}"[:38]
                  + f" {(ya[a]==ci).mean():9.3f} {(yb[a]==ci).mean():9.3f} "
                  f"{(yb[a]==ci).mean()-(ya[a]==ci).mean():+8.3f} {int(a.sum()):4d}{note}")
            for s in sorted(set(sp[a])):
                k = a & (sp == s)
                print(f"    └ {s:32s} {(ya[k]==ci).mean():9.3f} {(yb[k]==ci).mean():9.3f} "
                      f"{(yb[k]==ci).mean()-(ya[k]==ci).mean():+8.3f} {int(k.sum()):4d}")

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
