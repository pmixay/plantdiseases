#!/usr/bin/env python3
"""Export consented, consensus-labelled community photos for retraining.

Offline helper: run on the server with the same ``COMMUNITY_DB_PATH`` /
``COMMUNITY_THUMB_DIR`` as the API (it opens the database read-only), then
upload the output folder to Colab for notebook §2.

    python tools/export_community_labels.py --out /tmp/community-export

What is exported
* posts whose author switched on training consent, that an admin did not
  exclude, that are visible, and that have an agreed label
  (``community_labels``: expert-verified, or 3+ people with >= 70 %);
* the post's photos, plus answer photos whose author also consented.

What is never exported: account, actor or post ids, nicknames, any text, or
location. The plant group is exported as a one-way hash of the random
per-plant key, so a post and its recovery updates share ``capture_family_id``
and land in the same split.

Every row is ``split_hint=train_only``: community labels are weak labels and
must never grade a model until an offline review promotes them. Run
``tools/filter_supplement.py`` against the OOD benchmark before training to
catch re-posted benchmark photos.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

EXPORT_VERSION = "1"
COLUMNS = (
    "filename",
    "class",
    "split_hint",
    "capture_family_id",
    "immutable_source_id",
    "hn_kind",
    "license_id",
    "first_seen_model_version",
    "label_source",
    "agreement",
    "voters",
    "ai_disease",
    "ai_confidence",
    "photo_role",
    "dhash",
)


def _group_hash(plant_group: str) -> str:
    return "cp_" + hashlib.sha256(f"community-plant-v1|{plant_group}".encode()).hexdigest()[:20]


def _rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT p.id, p.plant_group, l.label, l.source, l.agreement, l.voters, l.ai_disease, "
        "l.ai_confidence, l.hard_example FROM forum_labels l "
        "JOIN forum_posts p ON p.id = l.post_id "
        "WHERE p.state = 'visible' AND p.training_consent = 1 AND p.training_excluded = 0 "
        "AND p.author_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
        "ORDER BY p.id"
    ).fetchall()


def _photos(conn: sqlite3.Connection, post_id: int) -> list[tuple[str, str, str]]:
    """``(filename, dhash, role)`` for the post's photos and consented answer photos."""
    own = conn.execute(
        "SELECT filename, COALESCE(phash, ''), 'post' FROM forum_media WHERE post_id = ? "
        "ORDER BY position",
        (post_id,),
    ).fetchall()
    answers = conn.execute(
        "SELECT m.filename, COALESCE(m.phash, ''), 'answer' FROM forum_media m "
        "JOIN forum_answers a ON a.id = m.answer_id "
        "WHERE a.post_id = ? AND a.state = 'visible' AND a.training_consent = 1 "
        "AND a.author_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
        "ORDER BY a.id, m.position",
        (post_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in [*own, *answers]]


def export(*, db_path: Path, media_dir: Path, out: Path, head_classes: set[str]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    images = out / "images"
    images.mkdir(exist_ok=True)
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = _rows(conn)
        written = []
        missing = 0
        seen: set[str] = set()
        for post_id, plant_group, label, source, agreement, voters, ai_disease, ai_conf, hard in rows:
            for filename, dhash, role in _photos(conn, int(post_id)):
                source_path = media_dir / filename
                if not source_path.is_file():
                    missing += 1
                    continue
                body = source_path.read_bytes()
                digest = hashlib.sha256(body).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                target_name = f"{digest[:24]}.jpg"
                shutil.copyfile(source_path, images / target_name)
                written.append(
                    {
                        "filename": f"images/{target_name}",
                        "class": label,
                        "split_hint": "train_only",
                        "capture_family_id": _group_hash(str(plant_group)),
                        "immutable_source_id": f"sha256:{digest}",
                        "hn_kind": "hard_example" if hard else "none",
                        "license_id": "community-consent",
                        "first_seen_model_version": "never",
                        "label_source": source,
                        "agreement": f"{float(agreement):.4f}",
                        "voters": int(voters),
                        "ai_disease": ai_disease or "",
                        "ai_confidence": "" if ai_conf is None else f"{float(ai_conf):.4f}",
                        "photo_role": role,
                        "dhash": dhash,
                    }
                )
    finally:
        conn.close()
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(written)
    by_class: dict[str, int] = {}
    for row in written:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1
    summary = {
        "export_version": EXPORT_VERSION,
        "exported_at": datetime.now(UTC).date().isoformat(),
        "posts": len(rows),
        "images": len(written),
        "missing_files": missing,
        "groups": len({row["capture_family_id"] for row in written}),
        "hard_examples": sum(1 for row in written if row["hn_kind"] == "hard_example"),
        "by_class": dict(sorted(by_class.items())),
        "out_of_head_classes": sorted(c for c in by_class if c not in head_classes),
        "split_policy": "train_only until offline review; never val_realworld or OOD",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=None, help="community.db (default: config)")
    parser.add_argument("--media", type=Path, default=None, help="forum photo folder")
    args = parser.parse_args(argv)
    import community  # noqa: PLC0415 — config is read at import
    import community_forum  # noqa: PLC0415

    try:
        from classifier import DEFAULT_CLASS_NAMES  # noqa: PLC0415

        head = set(DEFAULT_CLASS_NAMES)
    except Exception:  # noqa: BLE001 — summary field only
        head = set()
    db_path = args.db or Path(community.COMMUNITY_DB_PATH)
    media = args.media or community_forum.media_dir()
    summary = export(db_path=db_path, media_dir=media, out=args.out, head_classes=head)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
