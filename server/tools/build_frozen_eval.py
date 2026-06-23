#!/usr/bin/env python3
"""Freeze a held-out eval folder into the committed content-addressed manifest.

Run this **once on Colab** (or wherever the dataset is built) over a held-out
split — typically ``data_v2/classification/val/realworld`` right after ``§2``
populates it — to snapshot exactly which photos are the eval set. Commit the
resulting ``server/eval/frozen_eval_manifest.json`` to git. From then on the
notebook's ``§2`` pin (``frozen_eval.pin_frozen_eval``) reproduces that same set
on every run, so cross-version model A/B (``paired_eval.py``) is a clean paired
comparison instead of a moving target.

Identity is content (sha256 + 8×8 DCT pHash), read straight off the image bytes,
so the manifest survives renames, re-encodes and dataset-version churn.

By default every image in the folder is frozen. ``--sources`` restricts to
specific provenance (read from the per-image ``.json`` sidecar's ``source``) —
prefer **version-stable** sources for the pinned core; a churning supplement is
fine too since entries are content-keyed, but pin specific photos, not "whatever
v_n happened to hold out".

Usage
-----
    python -m server.tools.build_frozen_eval \\
        --eval-root data_v2/classification/val/realworld \\
        --out server/eval/frozen_eval_manifest.json \\
        [--sources doctorp inat plantdoc supplement] \\
        [--classes leaf_scorch_underwatering nutrient_deficiency] \\
        [--per-class-cap 120] [--seed 0] [--append]
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.frozen_eval import (
        DEFAULT_PHASH_THRESHOLD,
        FrozenEntry,
        FrozenManifest,
        iter_class_images,
        phash_file,
        sha256_file,
    )
except ImportError:  # pragma: no cover — run as a file from server/
    from frozen_eval import (  # type: ignore
        DEFAULT_PHASH_THRESHOLD,
        FrozenEntry,
        FrozenManifest,
        iter_class_images,
        phash_file,
        sha256_file,
    )


def _read_source(img: Path) -> str:
    sc = img.with_suffix(".json")
    if sc.exists():
        try:
            return json.loads(sc.read_text(encoding="utf-8")).get("source", "unknown")
        except Exception:
            return "unknown"
    return "unknown"


def build(
    eval_root: Path,
    *,
    sources: set[str] | None = None,
    classes: set[str] | None = None,
    per_class_cap: int | None = None,
    seed: int = 0,
) -> list[FrozenEntry]:
    rng = random.Random(seed)
    by_class: dict[str, list[FrozenEntry]] = {}
    for cls, img in iter_class_images(eval_root):
        if classes is not None and cls not in classes:
            continue
        src = _read_source(img)
        if sources is not None and src not in sources:
            continue
        entry = FrozenEntry(
            sha256=sha256_file(img),
            phash=phash_file(img),
            cls=cls,
            source=src,
            orig_name=img.name,
        )
        by_class.setdefault(cls, []).append(entry)

    entries: list[FrozenEntry] = []
    for cls in sorted(by_class):
        items = by_class[cls]
        if per_class_cap is not None and len(items) > per_class_cap:
            # Deterministic subsample so the manifest is reproducible.
            items = sorted(items, key=lambda e: e.sha256)
            rng.shuffle(items)
            items = items[:per_class_cap]
        # Stable, content-sorted order regardless of filesystem iteration.
        entries.extend(sorted(items, key=lambda e: e.sha256))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-root", type=Path, required=True,
                    help="held-out ImageFolder to freeze (e.g. val/realworld)")
    ap.add_argument("--out", type=Path,
                    default=Path("server/eval/frozen_eval_manifest.json"))
    ap.add_argument("--split", default="val_realworld")
    ap.add_argument("--sources", nargs="*", default=None,
                    help="restrict to these provenance sources (sidecar 'source')")
    ap.add_argument("--classes", nargs="*", default=None,
                    help="restrict to these class folders")
    ap.add_argument("--per-class-cap", type=int, default=None)
    ap.add_argument("--phash-threshold", type=int, default=DEFAULT_PHASH_THRESHOLD)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--append", action="store_true",
                    help="merge into an existing manifest (dedup by sha256)")
    args = ap.parse_args()

    new_entries = build(
        args.eval_root,
        sources=set(args.sources) if args.sources else None,
        classes=set(args.classes) if args.classes else None,
        per_class_cap=args.per_class_cap,
        seed=args.seed,
    )

    existing: list[FrozenEntry] = []
    if args.append and args.out.exists():
        existing = FrozenManifest.load(args.out).entries

    by_sha: dict[str, FrozenEntry] = {e.sha256: e for e in existing}
    for e in new_entries:
        by_sha[e.sha256] = e
    merged = sorted(by_sha.values(), key=lambda e: (e.cls, e.sha256))

    manifest = FrozenManifest(
        split=args.split,
        phash_threshold=args.phash_threshold,
        entries=merged,
        meta={
            "manifest_version": "1",
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "built_from": str(args.eval_root),
            "description": (
                "Content-addressed frozen eval set. Pinned into val_realworld by "
                "frozen_eval.pin_frozen_eval so cross-version model A/B is paired. "
                "Keyed by sha256 (exact) + 8x8 DCT pHash (re-encode fallback)."
            ),
        },
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    manifest.save(args.out)

    per_class: dict[str, int] = {}
    for e in merged:
        per_class[e.cls] = per_class.get(e.cls, 0) + 1
    print(f"wrote {args.out}  ({len(merged)} images, split={args.split})")
    for c in sorted(per_class):
        print(f"  {c:28s} {per_class[c]}")


if __name__ == "__main__":
    main()
