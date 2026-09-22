#!/usr/bin/env python3
"""QA-filter a downloaded supplement bundle before it is wired into training.

Why this exists
---------------
The v8 supplement ships 3,389 files and a manifest that says so. Neither number
is the one that matters. What matters is how many **distinct photographs** it
holds, whether any of them are the OOD benchmark we grade with, and whether the
serving pipeline would even accept them as input. This tool answers all three on
the actual bytes, and writes a filtered manifest that carries its verdict per
row.

The training notebook now requires and honours ``filter_verdict``. It also
requires ``immutable_source_id``, curator-assigned ``capture_family_id`` and
``first_seen_model_version``: pHash alone demonstrably misses rotated/noised
siblings, so this tool refuses to certify a production supplement without that
stable provenance contract.

The gates, and why each one is a gate
-------------------------------------
``bench_leak``
    Shared curator capture-family ID or pHash Hamming <= 6 against the benchmark.
    Stable family IDs are the rotation/noise-safe contract; pHash is an
    additional byte/content signal, not the claimed proof of independence. This is the expensive lesson
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
from pathlib import Path, PurePosixPath

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
    # Validate with hexdigits, NOT int(x, 16). int() accepts '0x' prefixes, '_'
    # separators and a leading '-'; _to_bits parses byte pairs with
    # int(h[i:i+2], 16) and accepts none of them, so a digest like
    # '0x123456789abcde' passed this guard and then crashed the whole run —
    # after the full-corpus decode — on a manifest defect this function exists
    # to absorb.
    if len(recorded) != 16 or any(c not in "0123456789abcdef" for c in recorded):
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

    if workers <= 0:
        raise ValueError("workers must be positive")
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXTS)
    with ThreadPool(workers) as pool:
        probed = pool.map(_probe, files)
    # Manifest paths are portable POSIX-relative paths. ``str(relative_path)``
    # emits backslashes on Windows, which made every valid manifest row look
    # missing and every on-disk image look orphaned on the user's machine.
    return {p.relative_to(root).as_posix(): m
            for p, m in zip(files, probed, strict=True)}


def _normalise_manifest_rows(rows: list[dict], *, require_provenance: bool = True) -> None:
    """Validate and canonicalise manifest identity fields in place.

    A row is joined to decoded bytes by ``filename``. Duplicate, absolute,
    traversing or class-disagreeing keys make that join ambiguous; accepting
    them would let one file inherit another row's label/verdict.
    """
    required = {"filename", "class"}
    if require_provenance:
        required |= {"immutable_source_id", "capture_family_id", "first_seen_model_version"}
    missing = required - set(rows[0])
    if missing:
        raise SystemExit(f"manifest missing required columns: {sorted(missing)}")
    seen: set[str] = set()
    source_ids: set[str] = set()
    family_contract: dict[str, tuple[str, str]] = {}
    for line_no, row in enumerate(rows, start=2):
        raw = (row.get("filename") or "").strip().replace("\\", "/")
        cls = (row.get("class") or "").strip()
        rel = PurePosixPath(raw)
        parts = rel.parts
        if (not raw or not cls or rel.is_absolute() or ".." in parts
                or raw.startswith("//") or (parts and ":" in parts[0])):
            raise SystemExit(f"manifest line {line_no}: unsafe/empty filename or class: {raw!r}")
        key = rel.as_posix().lstrip("./")
        if not key or key in seen:
            raise SystemExit(f"manifest line {line_no}: duplicate filename key {key!r}")
        if not parts or parts[0] != cls:
            raise SystemExit(
                f"manifest line {line_no}: class={cls!r} disagrees with filename={key!r}")
        seen.add(key)
        row["filename"] = key
        row["class"] = cls
        if require_provenance:
            source_id = (row.get("immutable_source_id") or "").strip()
            family_id = (row.get("capture_family_id") or "").strip()
            first_seen = (row.get("first_seen_model_version") or "").strip().lower()
            if not source_id or not family_id or not first_seen:
                raise SystemExit(
                    f"manifest line {line_no}: blank immutable source/capture/model provenance")
            if source_id in source_ids:
                raise SystemExit(
                    f"manifest line {line_no}: duplicate immutable_source_id {source_id!r}")
            if first_seen != "never":
                import re
                if re.fullmatch(r"v?\d+\.\d+\.\d+", first_seen) is None:
                    raise SystemExit(
                        f"manifest line {line_no}: invalid first_seen_model_version {first_seen!r}")
            contract = (cls, first_seen)
            if family_id in family_contract and family_contract[family_id] != contract:
                raise SystemExit(
                    f"manifest line {line_no}: capture_family_id {family_id!r} crosses "
                    "class/first-seen contracts")
            source_ids.add(source_id)
            family_contract[family_id] = contract


def _benchmark_family_ids(bench: Path) -> set[str]:
    """Load stable capture-family IDs; never claim rotation-safe audit without them."""
    manifests = [bench / "manifest.csv", bench / "manifest_arms.csv"]
    path = next((p for p in manifests if p.exists()), None)
    if path is None:
        raise RuntimeError(
            f"benchmark {bench} has no manifest with capture_family_id; "
            "independence cannot be certified")
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ids = [(row.get("capture_family_id") or "").strip() for row in rows]
    if not ids or not all(ids):
        raise RuntimeError(
            f"benchmark manifest {path} lacks capture_family_id on every row")
    return set(ids)


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


def filter_bundle(root: Path, bench: Path | None, workers: int, *,
                  require_provenance: bool = False) -> tuple[list[dict], dict]:
    """QA one supplement build on its bytes.

    ``require_provenance=False`` (the default since 2026-09-21) accepts a
    v8-shaped manifest (``filename``, ``class``, ``phash`` …) and clusters on
    pHash alone, recording ``legacy_phash_only_NOT_CERTIFIED`` as the
    independence method so the notebook can warn that rotated/noised siblings
    may hide. Pass ``True`` (``--require-provenance``) to demand
    ``immutable_source_id`` / ``capture_family_id`` /
    ``first_seen_model_version`` on every row and cluster on the curator's
    families; that is the v9 contract, not a precondition for running QA.
    """
    manifest_path = root / "manifest.csv"
    if not manifest_path.exists():
        sys.exit(f"no manifest at {manifest_path}")
    with manifest_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("manifest is empty")
    _normalise_manifest_rows(rows, require_provenance=require_provenance)

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

    # Benchmark leakage — the stop-the-line gate.
    leaks = 0
    if bench is not None:
        bench_family_ids = _benchmark_family_ids(bench) if require_provenance else set()
        bench_probes = _scan(bench, workers)
        bhex = [m["phash"] for m in bench_probes.values() if m["ok"]]
        if not bhex:
            raise RuntimeError(
                f"benchmark {bench} has no decodable images; leakage cannot be gated")
        bbits = _to_bits(bhex)
        print(f"benchmark: {len(bhex)} hashable images", flush=True)
        # Check every decodable manifest row, not only rows that survived the
        # earlier quality gates.  A blurry/tiny benchmark copy is still eval
        # contamination and must make the command exit non-zero.
        hashable = [i for i, h in enumerate(hashes) if h]
        hbits = _to_bits([hashes[i] for i in hashable])
        if bbits.size and hbits.size:
            nearest = _hamming(hbits, bbits).min(axis=1)
            for k, i in enumerate(hashable):
                same_family = ((rows[i].get("capture_family_id") or "").strip()
                               in bench_family_ids)
                if nearest[k] <= PHASH_MAX_HAMMING or same_family:
                    verdict[i] = "bench_leak"
                    leaks += 1

    live = [i for i, v in enumerate(verdict) if v == "keep"]
    bits = _to_bits([hashes[i] for i in live])
    cluster_of = _cluster(bits)

    # Merge the pHash graph with curator capture families.  The latter is what
    # catches arbitrary rotations/noise; pHash remains a useful independent
    # signal.  Single-link union is required when the two signals form a chain.
    parent = list(range(len(live)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)

    first_by_phash: dict[int, int] = {}
    first_by_family: dict[str, int] = {}
    for pos, row_idx in enumerate(live):
        phash_group = cluster_of[pos]
        if phash_group in first_by_phash:
            union(pos, first_by_phash[phash_group])
        else:
            first_by_phash[phash_group] = pos
        family = (rows[row_idx].get("capture_family_id") or rows[row_idx]["filename"]).strip()
        if family in first_by_family:
            union(pos, first_by_family[family])
        else:
            first_by_family[family] = pos
    roots: dict[int, int] = {}
    cluster_of = [roots.setdefault(find(i), len(roots)) for i in range(len(live))]

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
            "independence_method": (
                "curator_capture_family_id+phash_hamming<=6"
                if require_provenance else "legacy_phash_only_NOT_CERTIFIED"),
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
        r["filter_independence_method"] = (
            "capture_family_id+phash_v1" if require_provenance
            else "legacy_phash_only_NOT_CERTIFIED")
    return rows, report


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="supplement dir (holds manifest.csv)")
    ap.add_argument("--bench", type=Path, help="OOD benchmark dir to check leakage against")
    ap.add_argument("--out", type=Path, required=True, help="output prefix (.json + .csv)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--require-provenance", action="store_true",
                    help="demand immutable_source_id / capture_family_id / "
                         "first_seen_model_version on every row and cluster on the "
                         "curator's capture families (the v9 contract); default: "
                         "accept a v8-shaped manifest and cluster on pHash alone")
    args = ap.parse_args()

    rows, report = filter_bundle(args.root, args.bench, args.workers,
                                 require_provenance=args.require_provenance)

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
