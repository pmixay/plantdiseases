"""
Offline data export for analysis + training.

Bundles the three production stores — recorded scans + feedback corrections
(``labeling.db`` + ``labeling_images/``), raw feedback (``feedback.db`` +
``feedback_images/``) and community scans/opinions (``community.db`` +
``community_thumbs/``) — into a single timestamped archive the maintainer
pulls off the server box and folds into the offline dataset pipeline.

This replaces the removed human admin labeling queue: there is no in-app or
HTTP review surface anymore. Every recorded scan carries the model's own
prediction as a **weak label prior** (``model_pred`` / ``model_conf`` /
``model_set`` / ``status``); the maintainer judges the images offline with the
same vision-agent that grades scraped images (see ``docs/DATASET_LABELING_PLAN.md``).

What it does NOT do
-------------------
* Mutate any DB — every connection is opened read-only.
* Talk to the network — pure local SQLite + on-disk image dirs.
* Decode/judge images — that is the offline vision-agent's job.

Usage
-----
::

    # Write exports/plantvision_export_<UTC>.tar.gz (DBs as JSONL + images).
    python -m server.tools.export_data

    # Custom output dir; or an unpacked directory instead of a tarball.
    python -m server.tools.export_data --out /data/exports
    python -m server.tools.export_data --format dir

    # Skip the (large) image payload — just the structured rows.
    python -m server.tools.export_data --no-images
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow both ``python -m server.tools.export_data`` (repo root) and
# ``python tools/export_data.py`` (from server/).
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import community  # noqa: E402
import feedback  # noqa: E402
import labeling  # noqa: E402


# (db_path, image_dir, [tables]) for each store. Tables left None ⇒ every
# user table discovered at runtime (so schema migrations export automatically).
def _stores() -> list[dict]:
    return [
        {
            "name": "scans",
            "db": labeling.LABELING_DB_PATH,
            "images": labeling.LABELING_IMG_DIR,
            "tables": ["candidates"],
            "note": "recorded scans + feedback-derived corrections; "
                    "model_pred/model_conf/model_set/status are the weak label prior",
        },
        {
            "name": "feedback",
            "db": feedback.FEEDBACK_DB_PATH,
            "images": feedback.FEEDBACK_IMG_DIR,
            "tables": ["feedback"],  # issued_scan_ids is an abuse-guard, not data
            "note": "raw user corrections + the user_pseudo_id↔scan_id join",
        },
        {
            "name": "community",
            "db": community.COMMUNITY_DB_PATH,
            "images": community.COMMUNITY_THUMB_DIR,
            "tables": ["community_scans", "community_opinions"],
            "note": "community-shared scans + second-opinion suggestions (thumbnails)",
        },
    ]


def _connect_ro(db: Path) -> sqlite3.Connection | None:
    if not Path(db).exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _dump_table(conn: sqlite3.Connection, table: str, out_fh) -> int:
    """Write one JSONL row per DB row. Returns the row count."""
    n = 0
    try:
        cur = conn.execute(f"SELECT * FROM {table}")  # noqa: S608 - table from our allowlist
    except sqlite3.OperationalError:
        return 0  # table absent on this deployment
    for row in cur:
        out_fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        n += 1
    return n


def export(staging: Path, *, include_images: bool = True) -> dict:
    """Materialise the export tree under ``staging``. Returns the manifest dict."""
    staging.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_at_unix": int(time.time()),
        "include_images": include_images,
        "stores": {},
    }
    for store in _stores():
        name = store["name"]
        sdir = staging / name
        sdir.mkdir(parents=True, exist_ok=True)
        info: dict = {"note": store["note"], "tables": {}, "images": 0, "present": False}
        conn = _connect_ro(store["db"])
        if conn is not None:
            info["present"] = True
            try:
                for table in store["tables"]:
                    with (sdir / f"{table}.jsonl").open("w", encoding="utf-8") as fh:
                        info["tables"][table] = _dump_table(conn, table, fh)
            finally:
                conn.close()
        if include_images and Path(store["images"]).is_dir():
            dst = sdir / "images"
            dst.mkdir(exist_ok=True)
            count = 0
            for img in Path(store["images"]).iterdir():
                if img.is_file():
                    (dst / img.name).write_bytes(img.read_bytes())
                    count += 1
            info["images"] = count
        manifest["stores"][name] = info
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export production data for offline analysis/training.")
    ap.add_argument("--out", default="exports", help="output directory (default: ./exports)")
    ap.add_argument("--format", choices=["tar", "dir"], default="tar",
                    help="tar.gz archive (default) or an unpacked directory")
    ap.add_argument("--no-images", action="store_true", help="export DB rows only, skip images")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    include_images = not args.no_images

    if args.format == "dir":
        dest = out_dir / f"plantvision_export_{stamp}"
        manifest = export(dest, include_images=include_images)
        location = dest
    else:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / f"plantvision_export_{stamp}"
            manifest = export(staging, include_images=include_images)
            location = out_dir / f"plantvision_export_{stamp}.tar.gz"
            with tarfile.open(location, "w:gz") as tar:
                tar.add(staging, arcname=staging.name)

    total_rows = sum(sum(s["tables"].values()) for s in manifest["stores"].values())
    total_imgs = sum(s["images"] for s in manifest["stores"].values())
    print(f"Exported {total_rows} rows + {total_imgs} images -> {location}")
    for name, s in manifest["stores"].items():
        if not s["present"]:
            print(f"  {name:10s} (store not present on this deployment)")
            continue
        tbls = ", ".join(f"{t}={n}" for t, n in s["tables"].items())
        print(f"  {name:10s} {tbls}  images={s['images']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
