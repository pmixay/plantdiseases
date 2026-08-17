#!/usr/bin/env python3
"""QA-filter a downloaded supplement bundle before it is wired into training.

Why this exists
---------------
The v8 supplement ships 3,389 files and a manifest that says so. Neither number
is the one that matters. What matters is how many **distinct photographs** it
holds, whether any of them are the OOD benchmark we grade with, and whether the
serving pipeline would even accept them as input. This tool answers all three on
the actual bytes, and writes a filtered manifest that carries its verdict per
row so §2 of the notebook can honour it.

The gates, and why each one is a gate
-------------------------------------
``bench_leak``
    pHash Hamming <= 6 against the OOD benchmark. This is the expensive lesson
    of ``aditisalvi04``: a source that is genuinely never-trained-on is still
    poison if it is the eval set. A leaked row does not merely inflate a
    metric, it removes our ability to detect that the metric is inflated.

``dup_cross``
    The same photograph filed under two different classes. That is a label
    contradiction — at most one of the two can be right — so both copies are
    held out rather than guessed at.

``dup_intra``
    Near-duplicates within one class. Not wrong, but they are not supply
    either: they add gradient weight without adding information, and they make
    "663 leaf_scorch rows" read as far more coverage than it is. The first
    member of each cluster is kept, the rest are marked ``dup_intra``.

``blurry``
    ``pipeline._variance_of_laplacian`` below ``BLUR_LAPLACIAN_FLOOR`` — the
    *same function and threshold the server runs*, imported rather than
    reimplemented, so this can never drift from serving behaviour. A frame the
    blur gate short-circuits to ``not_a_plant`` at inference time is not a
    usable disease example. It is, however, a perfectly good ``not_a_plant``
    example, so the rule is skipped for that class.

``tiny`` / ``corrupt`` / ``missing`` / ``phash_mismatch``
    The image would be rejected at upload (``MIN_IMAGE_DIMENSION``), cannot be
    decoded, is absent from disk, or no longer hashes to within the dedup
    radius of what the manifest recorded — i.e. the download is not the
    dataset the manifest describes. Note *radius*, not equality: see
    ``_digest_drift``.

Everything is reported as **distinct pHash clusters per class**, never as file
counts. Folder sizes have been misleading this project for three releases: the
benchmark's own source folder holds ~1,400 files and 62 photographs.

Usage
-----
::

    python -m server.tools.filter_supplement \\
        --root  /path/to/supp \\
        --bench /path/to/oodbench \\
        --out   /path/to/report

Writes ``<out>.json`` (the full report) and ``<out>.csv`` (the input manifest
plus ``filter_verdict`` and ``cluster_id`` columns). Non-zero exit if any row
leaks the benchmark — that is a stop-the-line condition, not a warning.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


def _load_pipeline():
    """The server's ``pipeline`` module, whichever way this file was invoked.

    Imported rather than reimplemented so the blur threshold here cannot drift
    from what the pipeline enforces at request time. Resolves as a flat module
    (run from ``server/``), as ``server.pipeline`` (run from the repo root),
    and — when run as a bare script, which puts ``tools/`` rather than
    ``server/`` on the path — by adding the parent directory itself.
    """
    for attempt in range(3):
        try:
            if attempt == 2:
                from server import pipeline  # type: ignore[import-not-found]
            else:
                import pipeline  # type: ignore[import-not-found,no-redef]
            return pipeline
        except ImportError:
            if attempt == 0:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise ImportError("cannot locate the server 'pipeline' module from " + __file__)


_pipe = _load_pipeline()

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PHASH_MAX_HAMMING = 6      # docs/DATASET_LABELING_PLAN.md, "Image gates"
BLUR_EXEMPT = {"not_a_plant"}
BLUR_FLOOR = _pipe.BLUR_LAPLACIAN_FLOOR
MIN_SIDE = 48                                          # main.MIN_IMAGE_DIMENSION default
COLLECTION_MIN_SIDE = 384                              # DATASET_LABELING_PLAN.md "Image gates"


# ── hashing ──────────────────────────────────────────────────────────

_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _to_bits(hexes: list[str]) -> np.ndarray:
    """Pack 16-hex-char pHashes into an (n, 8) uint8 matrix for fast Hamming."""
    if not hexes:
        return np.zeros((0, 8), dtype=np.uint8)
    return np.array(
        [[int(h[i:i + 2], 16) for i in range(0, 16, 2)] for h in hexes],
        dtype=np.uint8,
    )


def _hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise Hamming distance between two packed pHash matrices."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.int16)
    xor = np.bitwise_xor(a[:, None, :], b[None, :, :])
    return _POPCOUNT[xor].sum(axis=2).astype(np.int16)


def _digest_drift(recorded: str, recomputed: str) -> int | None:
    """Hamming between a manifest digest and the recomputed one, or None.

    Compared with a tolerance rather than for equality, and this is not a
    nicety: 131 of the v8 supplement's 3,389 rows fail exact equality and every
    single one of them differs by 2 or 4 bits — JPEG re-encoding during bundle
    assembly, not a different photograph. Exact equality would have condemned
    3.9% of a clean corpus. Two digests describe the same image exactly when
    they are inside the radius the rest of this tool calls "the same image".

    Returns None when the recorded digest is absent or not a 64-bit hex value,
    which is a manifest defect rather than an image defect and is counted
    separately instead of condemning the row.
    """
    recorded = (recorded or "").strip().lower()
    if len(recorded) != 16:
        return None
    try:
        int(recorded, 16)
    except ValueError:
        return None
    return int(_hamming(_to_bits([recorded]), _to_bits([recomputed]))[0, 0])


def _probe(path: Path) -> dict:
    """Decode once and return every per-image measurement we need."""
    import imagehash
    from PIL import Image

    out: dict = {"ok": False}
    try:
        with Image.open(path) as im:
            im.load()
            rgb = im.convert("RGB")
            out["w"], out["h"] = rgb.size
            out["phash"] = str(imagehash.phash(rgb))
            out["blur"] = float(_pipe._variance_of_laplacian(rgb))
            out["ok"] = True
    except Exception as exc:                           # noqa: BLE001 — any decode failure is `corrupt`
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _scan(root: Path, workers: int) -> dict[str, dict]:
    """Probe every image under ``root``, keyed by path relative to it."""
    from multiprocessing.pool import ThreadPool

    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXTS)
    with ThreadPool(workers) as pool:
        probed = pool.map(_probe, files)
    return {str(p.relative_to(root)): m for p, m in zip(files, probed, strict=True)}


# ── clustering ───────────────────────────────────────────────────────


def _cluster(bits: np.ndarray, block: int = 512) -> list[int]:
    """Single-linkage cluster ids over Hamming <= PHASH_MAX_HAMMING.

    Blocked so the pairwise matrix never materialises in full: at 3.4k rows the
    dense form is only ~11M cells, but a supplement three times this size would
    not fit, and this is the tool that will be pointed at v9.
    """
    n = bits.shape[0]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for start in range(0, n, block):
        stop = min(start + block, n)
        d = _hamming(bits[start:stop], bits)
        for i, j in zip(*np.where(d <= PHASH_MAX_HAMMING), strict=True):
            gi = start + int(i)
            if gi != int(j):
                union(gi, int(j))

    roots = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(n)]


# ── reporting ────────────────────────────────────────────────────────


def _resolution_stats(sides: list[int]) -> dict:
    """Short-side percentiles and compliance with the *collection* floor.

    Reported, never enforced. The collection floor (docs/DATASET_LABELING_PLAN.md,
    "Image gates": short side >= 384 px) governs what a *new* round should
    accept; rows already published and already trained on are a different
    question, and silently dropping them here would rewrite the corpus that
    every shipped bundle was fitted to. The number belongs in a report so a
    collection round can act on it deliberately.
    """
    if not sides:
        return {"short_side_p05": None, "short_side_median": None,
                "below_collection_floor": 0}
    arr = np.array(sides)
    return {
        "short_side_p05": int(np.percentile(arr, 5)),
        "short_side_median": int(np.median(arr)),
        "below_collection_floor": int((arr < COLLECTION_MIN_SIDE).sum()),
    }


# ── main ─────────────────────────────────────────────────────────────


def filter_bundle(root: Path, bench: Path | None, workers: int) -> tuple[list[dict], dict]:
    manifest_path = root / "manifest.csv"
    if not manifest_path.exists():
        sys.exit(f"no manifest at {manifest_path}")
    with manifest_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("manifest is empty")

    print(f"manifest: {len(rows)} rows", flush=True)
    probes = _scan(root, workers)
    print(f"decoded:  {len(probes)} files on disk", flush=True)

    named = {r["filename"] for r in rows}
    orphans = sorted(set(probes) - named)

    # Per-row verdicts, cheapest first so an unusable row never reaches the
    # expensive gates and never pollutes the cluster graph.
    verdict: list[str] = []
    hashes: list[str] = []
    sides: list[int] = []
    unreadable_digests = 0
    for r in rows:
        p = probes.get(r["filename"])
        if p is None:
            verdict.append("missing")
            hashes.append("")
            sides.append(0)
            continue
        if not p["ok"]:
            verdict.append("corrupt")
            hashes.append("")
            sides.append(0)
            continue
        hashes.append(p["phash"])
        sides.append(min(p["w"], p["h"]))
        drift = _digest_drift(r.get("phash", ""), p["phash"])
        if drift is None:
            unreadable_digests += 1
        if min(p["w"], p["h"]) < MIN_SIDE:
            verdict.append("tiny")
        elif drift is not None and drift > PHASH_MAX_HAMMING:
            verdict.append("phash_mismatch")
        elif p["blur"] < BLUR_FLOOR and r["class"] not in BLUR_EXEMPT:
            verdict.append("blurry")
        else:
            verdict.append("keep")

    live = [i for i, v in enumerate(verdict) if v == "keep"]
    bits = _to_bits([hashes[i] for i in live])

    # Benchmark leakage — the stop-the-line gate.
    leaks = 0
    if bench is not None:
        bench_probes = _scan(bench, workers)
        bhex = [m["phash"] for m in bench_probes.values() if m["ok"]]
        bbits = _to_bits(bhex)
        print(f"benchmark: {len(bhex)} hashable images", flush=True)
        if bbits.size and bits.size:
            nearest = _hamming(bits, bbits).min(axis=1)
            for k, i in enumerate(live):
                if nearest[k] <= PHASH_MAX_HAMMING:
                    verdict[i] = "bench_leak"
                    leaks += 1

    live = [i for i, v in enumerate(verdict) if v == "keep"]
    bits = _to_bits([hashes[i] for i in live])
    cluster_of = _cluster(bits)

    # A cluster spanning two classes is a label contradiction; within one class
    # it is redundancy. Order in the manifest decides which copy survives.
    members: dict[int, list[int]] = defaultdict(list)
    for k, i in enumerate(live):
        members[cluster_of[k]].append(i)

    cluster_id = [-1] * len(rows)
    for k, i in enumerate(live):
        cluster_id[i] = cluster_of[k]

    for idxs in members.values():
        classes = {rows[i]["class"] for i in idxs}
        if len(classes) > 1:
            for i in idxs:
                verdict[i] = "dup_cross"
            continue
        for i in idxs[1:]:
            verdict[i] = "dup_intra"

    # Report on distinct clusters, never on file counts.
    kept = [i for i, v in enumerate(verdict) if v == "keep"]
    per_class: dict[str, dict] = {}
    for cls in sorted({r["class"] for r in rows}):
        idxs = [i for i, r in enumerate(rows) if r["class"] == cls]
        k = [i for i in idxs if verdict[i] == "keep"]
        per_class[cls] = {
            "manifest_rows": len(idxs),
            "distinct_clusters": len(k),
            "dropped": len(idxs) - len(k),
            "inflation": round(len(idxs) / max(len(k), 1), 2),
            "reasons": dict(Counter(verdict[i] for i in idxs if verdict[i] != "keep")),
            "eval_ok_kept": sum(1 for i in k if rows[i].get("split_hint") == "eval_ok"),
            **_resolution_stats([sides[i] for i in k]),
        }

    report = {
        "root": str(root),
        "benchmark": str(bench) if bench else None,
        "thresholds": {
            "phash_max_hamming": PHASH_MAX_HAMMING,
            "blur_laplacian_floor": BLUR_FLOOR,
            "min_side_px": MIN_SIDE,
            "collection_min_side_px": COLLECTION_MIN_SIDE,
            "blur_exempt_classes": sorted(BLUR_EXEMPT),
        },
        "totals": {
            "manifest_rows": len(rows),
            "files_on_disk": len(probes),
            "orphan_files": len(orphans),
            "kept": len(kept),
            "dropped": len(rows) - len(kept),
            "benchmark_leaks": leaks,
            "unreadable_manifest_digests": unreadable_digests,
            "below_collection_floor": sum(
                v["below_collection_floor"] for v in per_class.values()),
        },
        "verdicts": dict(Counter(verdict)),
        "per_class": per_class,
        "orphans_sample": orphans[:20],
    }
    for i, r in enumerate(rows):
        r["filter_verdict"] = verdict[i]
        r["cluster_id"] = cluster_id[i]
    return rows, report


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="supplement dir (holds manifest.csv)")
    ap.add_argument("--bench", type=Path, help="OOD benchmark dir to check leakage against")
    ap.add_argument("--out", type=Path, required=True, help="output prefix (.json + .csv)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rows, report = filter_bundle(args.root, args.bench, args.workers)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2))
    with args.out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    t = report["totals"]
    print(f"\n{'class':28s} {'rows':>6s} {'distinct':>9s} {'infl':>6s} "
          f"{'shortp05':>9s} {'<384':>6s}  dropped for")
    for cls, s in report["per_class"].items():
        why = ", ".join(f"{k}={v}" for k, v in sorted(s["reasons"].items())) or "-"
        print(f"{cls:28s} {s['manifest_rows']:6d} {s['distinct_clusters']:9d} "
              f"{s['inflation']:6.2f} {str(s['short_side_p05']):>9s} "
              f"{s['below_collection_floor']:6d}  {why}")
    print(f"\nkept {t['kept']}/{t['manifest_rows']} rows "
          f"({t['dropped']} dropped), {t['orphan_files']} orphan files on disk")
    print(f"wrote {args.out.with_suffix('.json')} and {args.out.with_suffix('.csv')}")

    if t["benchmark_leaks"]:
        sys.exit(f"\nSTOP: {t['benchmark_leaks']} rows match the OOD benchmark at "
                 f"Hamming <= {PHASH_MAX_HAMMING}. The eval set is inside the training "
                 f"corpus — fix this before any training run.")


if __name__ == "__main__":
    main()
