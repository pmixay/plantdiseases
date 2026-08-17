#!/usr/bin/env python3
"""List a Kaggle dataset's files EXHAUSTIVELY, and gate it against the benchmark.

Why this exists
---------------
The 2026-08-15 data survey concluded that public abiotic-scorch imagery was
exhausted. That conclusion rested on a lister that capped pagination at 40 pages
and **returned silently** when the cap was hit. Re-listing one dataset without
the cap returned 49,878 files where the survey had recorded ~24,000 — and the
folders it had never seen included an explicit ``Hibiscus_Scorch``. A negative
result from a truncating lister is not evidence of absence, and 207 datasets'
worth of "nothing here" was produced that way.

So this tool raises rather than truncates, and it pairs the listing with the one
gate that actually decides usability:

**pHash against the OOD benchmark.** A dataset being *never trained on* does not
make it usable — it must also not be the thing we grade with. ``aditisalvi04``
looked like exactly the missing supply (1,200 ``Hibiscus_Scorch`` files) and
turned out to be the benchmark's own photographs: 60/60 sampled at Hamming 0.
Wiring it in would have destroyed the only clean eval set this project has,
and every metric would merely have improved.

Usage
-----
::

    export KAGGLE_API_TOKEN=...
    # what is actually in there?
    python -m server.tools.kaggle_inventory --list owner/slug

    # is folder X usable, or is it the benchmark?
    python -m server.tools.kaggle_inventory --gate owner/slug \\
        --folder Hibiscus_Scorch --bench oodbench --sample 60
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.kaggle.com/api/v1/"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PHASH_MAX_HAMMING = 6          # DATASET_LABELING_PLAN.md "Image gates"


def _token() -> str:
    tok = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not tok:
        sys.exit("KAGGLE_API_TOKEN is not set")
    return tok


def _api(path: str, tries: int = 5):
    for i in range(tries):
        try:
            req = urllib.request.Request(
                API + path,
                headers={"Authorization": "Bearer " + _token(), "User-Agent": "curl/8"})
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception as e:                      # noqa: BLE001 — retry on 429/5xx
            if "429" in str(e) or "50" in str(e)[:3]:
                time.sleep(5 * (i + 1))
                continue
            raise
    raise RuntimeError(f"rate-limited after {tries} attempts: {path}")


def listing(ref: str, hard_cap: int = 2000) -> list[str]:
    """Every file name in the dataset. RAISES if the cap is reached with a live
    page token — the failure mode this module exists to prevent."""
    names: list[str] = []
    tok = None
    for _page in range(hard_cap):
        url = (f"datasets/list/{ref}?pageSize=200"
               + (f"&pageToken={urllib.parse.quote(tok)}" if tok else ""))
        r = _api(url)
        files = r.get("datasetFiles") or []
        names += [f.get("name") or "" for f in files]
        tok = r.get("nextPageToken")
        if not tok or not files:
            return names
    raise RuntimeError(
        f"{ref}: hit the {hard_cap}-page cap with nextPageToken still live after "
        f"{len(names)} files. The listing is INCOMPLETE — raise hard_cap rather "
        f"than trusting anything derived from it.")


def download_file(ref: str, name: str, dest: Path) -> bool:
    url = (f"{API}datasets/download/{ref}?file_name={urllib.parse.quote(name)}")
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + _token(), "User-Agent": "curl/8"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(urllib.request.urlopen(req, timeout=180).read())
        return True
    except Exception:                               # noqa: BLE001
        return False


def _phash(path: Path):
    import imagehash
    from PIL import Image
    try:
        with Image.open(path) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception:                               # noqa: BLE001
        return None


def gate(ref: str, folder: str, bench: Path, sample: int, workdir: Path) -> dict:
    """Sample a folder and pHash it against the benchmark. The verdict that
    decides whether a source is supply or poison."""
    names = [n for n in listing(ref) if folder.lower() in n.lower()
             and Path(n).suffix.lower() in IMG_EXTS]
    if not names:
        return {"ref": ref, "folder": folder, "verdict": "EMPTY", "files": 0}
    names.sort()
    step = max(1, len(names) // sample)
    sel = names[::step][:sample]

    got = []
    for n in sel:
        dst = workdir / n.replace("/", "__")
        if dst.exists() or download_file(ref, n, dst):
            got.append(dst)
        time.sleep(0.4)                             # Kaggle rate-limits hard

    bench_hashes = [h for h in
                    (_phash(p) for p in sorted(Path(bench).rglob("*"))
                     if p.suffix.lower() in IMG_EXTS)
                    if h is not None]
    hits, dists, kept = 0, [], []
    for p in got:
        h = _phash(p)
        if h is None:
            continue
        d = min((h - b) for b in bench_hashes) if bench_hashes else 99
        dists.append(d)
        if d <= PHASH_MAX_HAMMING:
            hits += 1
        elif not any((h - k) <= PHASH_MAX_HAMMING for k in kept):
            kept.append(h)                          # distinct within the sample

    n = len(dists)
    verdict = ("CONTAMINATED — IS the benchmark, deny-list it" if n and hits / n > 0.5
               else "partially contaminated — inspect" if hits
               else "clean of the benchmark")
    return {"ref": ref, "folder": folder, "files": len(names), "sampled": n,
            "benchmark_hits_at_hamming6": hits,
            "min_distance": min(dists) if dists else None,
            "median_distance": sorted(dists)[n // 2] if n else None,
            "distinct_in_sample": len(kept),
            "augmentation_ratio": round(n / max(len(kept), 1), 2),
            "verdict": verdict}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", metavar="REF", help="owner/slug to inventory")
    ap.add_argument("--gate", metavar="REF", help="owner/slug to pHash-gate")
    ap.add_argument("--folder", default="", help="folder substring (with --gate)")
    ap.add_argument("--bench", type=Path, default=Path("oodbench"))
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/kaggle_gate"))
    args = ap.parse_args()

    if args.list:
        names = listing(args.list)
        folders = collections.Counter("/".join(n.split("/")[:-1]) for n in names
                                      if Path(n).suffix.lower() in IMG_EXTS)
        print(f"{args.list}: {len(names)} files, {len(folders)} image folders")
        for k, v in folders.most_common():
            print(f"  {v:7d}  {k}")
        return
    if args.gate:
        if not args.folder:
            sys.exit("--gate needs --folder")
        print(json.dumps(gate(args.gate, args.folder, args.bench, args.sample,
                              args.workdir), indent=2))
        return
    ap.error("give --list or --gate")


if __name__ == "__main__":
    main()
