#!/usr/bin/env python3
"""Export every consented training photo as one privacy-checked archive.

The one path from production data to training: run it in the API container,
take the archive offline, judge and label the photos there, then add them to
the dataset. No notebook cell reads this archive directly.

    python tools/export_training_bundle.py --output-dir /tmp/exports

Both stores are opened through SQLite's read-only URI; the paths default to
the server's own configuration.

What goes in
* the training store: every retained, consented scan crop, as captured;
* the forum: photos of visible posts whose author switched on training
  consent, and photos of visible answers whose author did, unless an admin
  excluded the post or the author deleted their account.

Labels are hints, never ground truth: the model's verdict, the user's
feedback, the post author's stated condition, an answer's diagnosis and the
community consensus ride along under ``hints``, each as recorded.

Every photo gets a ``capture_family``: photos of one forum plant, exact copies
and near-duplicates (perceptual hash within ``NEAR_DUPLICATE_DISTANCE``, across
both stores) share one, so a family lands in one split.

Never exported: account, actor, post or answer ids, nicknames, titles, bodies
or any other free text, location, or image metadata. Every image is
hash-checked and must carry no EXIF/XMP/IPTC/ICC block, or nothing is written.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import tarfile
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

try:
    from .. import feedback_labels, training_store
except ImportError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import feedback_labels
    import training_store


BUNDLE_VERSION = "1"
NEAR_DUPLICATE_DISTANCE = 8
_PHASH = re.compile(r"[0-9a-f]{16}\Z")
_TRAINING_IMAGE = re.compile(r"[0-9a-f-]{36}\.jpg\Z")
_FORUM_IMAGE = re.compile(r"[A-Za-z0-9_-]{1,96}\.jpg\Z")

_TRAINING_COLUMNS = (
    "sample_id",
    "image_key",
    "sha256",
    "perceptual_hash",
    "created_day",
    "source",
    "model_family",
    "model_version",
    "predicted_class",
    "predicted_confidence",
    "top_k_json",
    "conformal_set_json",
    "warnings_json",
    "blur_bucket",
    "detector_region_count",
    "bbox_area_buckets_json",
    "severity",
    "user_correct",
    "corrected_class",
    "correction_reason",
    "out_of_head",
    "review_status",
    "label_provenance",
    "consent_version",
)


class ExportError(RuntimeError):
    """Raised when a source cannot satisfy the export privacy contract."""


# ── reading the stores ─────────────────────────────────────────────────


def _read_only(database: Path, what: str) -> sqlite3.Connection:
    if not database.is_file():
        raise ExportError(f"{what} database does not exist")
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _json_list(raw: str, field: str) -> list:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ExportError(f"invalid structured field: {field}") from exc
    if not isinstance(value, list):
        raise ExportError(f"invalid structured field: {field}")
    return value


def _label(value: object) -> str | None:
    """A class key, or None. Anything else could be free text: never export it."""
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in feedback_labels.FEEDBACK_ALLOWED_CORRECTIONS else None


def _checked_image(body: bytes) -> str:
    """The perceptual hash of a metadata-free JPEG; refuse anything else."""
    if not training_store.metadata_is_absent(body):
        raise ExportError("image contains metadata")
    try:
        with Image.open(io.BytesIO(body)) as image:
            return training_store.perceptual_hash(image)
    except (OSError, UnidentifiedImageError) as exc:
        raise ExportError("image cannot be decoded") from exc


def _training_usage(review_status: str) -> str:
    if review_status == "offline_yes":
        return "evaluation_eligible"
    if review_status in {"offline_no", "offline_unsure", "pending"}:
        return "review_required"
    return "train_only"


def _training_samples(database: Path, images: Path) -> list[dict]:
    if not images.is_dir():
        raise ExportError("training image directory does not exist")
    columns = ", ".join(_TRAINING_COLUMNS)
    with closing(_read_only(database, "training")) as connection:
        connection.execute("BEGIN")
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    f"SELECT {columns} FROM training_samples "
                    "WHERE prune_status = 'retained' ORDER BY sample_id"
                )
            ]
        except sqlite3.DatabaseError as exc:
            raise ExportError("training database schema is invalid") from exc
        finally:
            connection.execute("ROLLBACK")

    samples = []
    for row in rows:
        image_key = str(row["image_key"])
        if _TRAINING_IMAGE.fullmatch(image_key) is None:
            raise ExportError("unsafe training image key")
        if _PHASH.fullmatch(str(row["perceptual_hash"])) is None:
            raise ExportError("invalid perceptual hash")
        try:
            body = (images / image_key).read_bytes()
        except OSError as exc:
            raise ExportError("training image is missing") from exc
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ExportError("training image checksum mismatch")
        _checked_image(body)
        user_correct = row["user_correct"]
        samples.append(
            {
                "sample_id": str(row["sample_id"]),
                "origin": "training_store",
                "image": f"images/{image_key}",
                "sha256": row["sha256"],
                "perceptual_hash": row["perceptual_hash"],
                "created_day": row["created_day"],
                "offline_usage": _training_usage(str(row["review_status"])),
                "hints": {
                    "model": {
                        "class": row["predicted_class"],
                        "confidence": row["predicted_confidence"],
                        "family": row["model_family"],
                        "version": row["model_version"],
                    },
                    "user_feedback": None
                    if user_correct is None
                    else {
                        "correct": bool(user_correct),
                        "corrected_class": row["corrected_class"],
                        "reason": row["correction_reason"],
                        "out_of_head": None
                        if row["out_of_head"] is None
                        else bool(row["out_of_head"]),
                    },
                },
                "training_store": {
                    "source": row["source"],
                    "top_k": _json_list(row["top_k_json"], "top_k"),
                    "conformal_set": _json_list(row["conformal_set_json"], "conformal_set"),
                    "warnings": _json_list(row["warnings_json"], "warnings"),
                    "blur_bucket": row["blur_bucket"],
                    "detector_region_count": row["detector_region_count"],
                    "bbox_area_buckets": _json_list(
                        row["bbox_area_buckets_json"], "bbox_area_buckets"
                    ),
                    "severity": row["severity"],
                    "review_status": row["review_status"],
                    "label_provenance": row["label_provenance"],
                    "consent_version": row["consent_version"],
                },
                "_body": body,
                "_family_key": None,
            }
        )
    return samples


def _forum_photos(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    tables = _tables(connection)
    if not {"forum_posts", "forum_answers", "forum_media"} <= tables:
        return []
    deleted = (
        "SELECT actor_id FROM community_deleted_actors"
        if "community_deleted_actors" in tables
        else "SELECT NULL WHERE 0"
    )
    label = (
        "l.label, l.source, l.agreement, l.voters, l.hard_example"
        if "forum_labels" in tables
        else "NULL, NULL, NULL, NULL, NULL"
    )
    label_join = "LEFT JOIN forum_labels l ON l.post_id = p.id" if "forum_labels" in tables else ""
    visible_post = (
        "p.state = 'visible' AND p.training_excluded = 0 "
        f"AND p.author_actor_id NOT IN ({deleted})"
    )
    post_photos = (
        f"SELECT m.filename, m.created_at, p.plant_group, 'post' AS role, "
        f"p.condition, NULL AS diagnosis, p.ai_disease, p.ai_confidence, {label} "
        f"FROM forum_media m JOIN forum_posts p ON p.id = m.post_id {label_join} "
        f"WHERE m.answer_id IS NULL AND p.training_consent = 1 AND {visible_post}"
    )
    answer_photos = (
        f"SELECT m.filename, m.created_at, p.plant_group, 'answer' AS role, "
        f"p.condition, a.diagnosis, p.ai_disease, p.ai_confidence, {label} "
        f"FROM forum_media m JOIN forum_answers a ON a.id = m.answer_id "
        f"JOIN forum_posts p ON p.id = a.post_id {label_join} "
        f"WHERE a.state = 'visible' AND a.training_consent = 1 "
        f"AND a.author_actor_id NOT IN ({deleted}) AND {visible_post}"
    )
    return connection.execute(
        f"{post_photos} UNION ALL {answer_photos} ORDER BY 2, 1"
    ).fetchall()


def _plant_family_key(plant_group: str) -> str:
    return hashlib.sha256(f"community-plant-v1|{plant_group}".encode()).hexdigest()


def _forum_samples(database: Path, media: Path) -> tuple[list[dict], int]:
    """The forum's consented photos, and how many were listed but missing."""
    with closing(_read_only(database, "community")) as connection:
        connection.execute("BEGIN")
        try:
            rows = _forum_photos(connection)
        except sqlite3.DatabaseError as exc:
            raise ExportError("community database schema is invalid") from exc
        finally:
            connection.execute("ROLLBACK")

    samples: list[dict] = []
    seen: set[str] = set()
    missing = 0
    for row in rows:
        filename = str(row["filename"])
        if _FORUM_IMAGE.fullmatch(filename) is None:
            raise ExportError("unsafe forum image name")
        try:
            body = (media / filename).read_bytes()
        except OSError:
            # Forum photos can be cleaned up before their rows; skip, count.
            missing += 1
            continue
        digest = hashlib.sha256(body).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        phash = _checked_image(body)
        sample_id = f"forum-{digest[:32]}"
        community = None
        if row["label"] is not None:
            community = {
                "class": _label(row["label"]),
                "source": row["source"],
                "agreement": round(float(row["agreement"]), 4),
                "voters": int(row["voters"]),
                "hard_example": bool(row["hard_example"]),
            }
        samples.append(
            {
                "sample_id": sample_id,
                "origin": "forum",
                "image": f"images/{sample_id}.jpg",
                "sha256": digest,
                "perceptual_hash": phash,
                "created_day": datetime.fromtimestamp(int(row["created_at"]), UTC)
                .date()
                .isoformat(),
                "offline_usage": "train_only",
                "hints": {
                    "model": None
                    if _label(row["ai_disease"]) is None
                    else {
                        "class": _label(row["ai_disease"]),
                        "confidence": None
                        if row["ai_confidence"] is None
                        else round(float(row["ai_confidence"]), 4),
                    },
                    "author_condition": _label(row["condition"]),
                    "answer_diagnosis": _label(row["diagnosis"]),
                    "community": community,
                },
                "forum": {"photo_role": row["role"]},
                "_body": body,
                "_family_key": _plant_family_key(str(row["plant_group"])),
            }
        )
    return samples, missing


# ── capture families ───────────────────────────────────────────────────


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


class _BKNode:
    __slots__ = ("index", "value", "children")

    def __init__(self, value: str, index: int):
        self.value = value
        self.index = index
        self.children: dict[int, _BKNode] = {}

    def add(self, value: str, index: int) -> None:
        node = self
        while True:
            distance = _hamming(value, node.value)
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKNode(value, index)
                return
            node = child

    def query(self, value: str, limit: int) -> Iterable[int]:
        distance = _hamming(value, self.value)
        if distance <= limit:
            yield self.index
        for edge, child in self.children.items():
            if distance - limit <= edge <= distance + limit:
                yield from child.query(value, limit)


def _assign_families(samples: list[dict]) -> list[dict]:
    """Set ``capture_family`` on every sample; return the multi-member families."""
    union = _UnionFind(len(samples))
    tree: _BKNode | None = None
    first_by_key: dict[str, int] = {}
    for index, sample in enumerate(samples):
        for key in (f"sha256:{sample['sha256']}", sample["_family_key"]):
            if key is None:
                continue
            if key in first_by_key:
                union.union(first_by_key[key], index)
            else:
                first_by_key[key] = index
        phash = sample["perceptual_hash"]
        if tree is None:
            tree = _BKNode(phash, index)
        else:
            for match in tree.query(phash, NEAR_DUPLICATE_DISTANCE):
                union.union(index, match)
            tree.add(phash, index)

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(len(samples)):
        members[union.find(index)].append(index)
    families = []
    for indexes in members.values():
        sample_ids = sorted(samples[index]["sample_id"] for index in indexes)
        family = "family-" + hashlib.sha256("\n".join(sample_ids).encode()).hexdigest()[:16]
        for index in indexes:
            samples[index]["capture_family"] = family
        if len(indexes) > 1:
            families.append({"capture_family": family, "sample_ids": sample_ids})
    return sorted(families, key=lambda item: item["capture_family"])


# ── the archive ────────────────────────────────────────────────────────


def _hint_class(sample: dict, hint: str) -> str:
    value = sample["hints"].get(hint)
    return str(value["class"]) if value and value.get("class") else "none"


def _inventory(samples: list[dict]) -> dict:
    def counts(values: Iterable[str]) -> dict[str, int]:
        return dict(sorted(Counter(values).items()))

    return {
        "total": len(samples),
        "by_origin": counts(s["origin"] for s in samples),
        "by_offline_usage": counts(s["offline_usage"] for s in samples),
        "by_model_hint": counts(_hint_class(s, "model") for s in samples),
        "by_community_hint": counts(_hint_class(s, "community") for s in samples),
        "capture_families": len({s["capture_family"] for s in samples}),
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def _tar_info(name: str, body: bytes, timestamp: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = 0o444
    info.mtime = timestamp
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _write_archive(destination: Path, members: dict[str, bytes], timestamp: int) -> None:
    checksums = "".join(
        f"{hashlib.sha256(body).hexdigest()}  {name}\n" for name, body in sorted(members.items())
    ).encode("ascii")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        with tarfile.open(temporary, "w:gz", compresslevel=9) as archive:
            for name, body in sorted({**members, "checksums.sha256": checksums}.items()):
                archive.addfile(_tar_info(name, body, timestamp), io.BytesIO(body))
        temporary.chmod(0o400)
        os.replace(temporary, destination)
        temporary = None
        destination.chmod(0o444)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def export_bundle(
    output_directory: Path,
    *,
    training: tuple[Path, Path] | None,
    community: tuple[Path, Path] | None,
    now: datetime | None = None,
) -> Path:
    """Write one timestamped, read-only archive, or fail without one.

    ``training`` is ``(training.db, image directory)`` and ``community`` is
    ``(community.db, forum photo directory)``; ``None`` leaves a store out.
    """
    if training is None and community is None:
        raise ExportError("no store to export")
    generated = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    timestamp = int(generated.timestamp())
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / f"training-bundle-{generated:%Y%m%dT%H%M%SZ}.tar.gz"
    if destination.exists():
        raise ExportError("export archive already exists")

    samples = _training_samples(*map(Path, training)) if training else []
    missing_forum_files = 0
    if community:
        forum, missing_forum_files = _forum_samples(*map(Path, community))
        samples += forum
    families = _assign_families(samples)

    manifest = [
        {key: value for key, value in sample.items() if not key.startswith("_")}
        for sample in samples
    ]
    members = {
        "metadata.json": _json_bytes(
            {
                "bundle_version": BUNDLE_VERSION,
                "training_store_schema_version": training_store.SCHEMA_VERSION,
                "generated_at": generated.isoformat().replace("+00:00", "Z"),
                "stores": sorted(
                    name for name, store in (("training_store", training), ("forum", community))
                    if store
                ),
                "sample_count": len(samples),
                "missing_forum_files": missing_forum_files,
                "label_policy": "hints_only_until_offline_review",
                "split_policy": "capture_family_locked",
            }
        ),
        "manifest.jsonl": b"".join(
            json.dumps(item, ensure_ascii=True, sort_keys=True).encode() + b"\n"
            for item in manifest
        ),
        "inventory.json": _json_bytes(_inventory(samples)),
        "families.json": _json_bytes(
            {"near_duplicate_hamming_distance": NEAR_DUPLICATE_DISTANCE, "families": families}
        ),
    }
    for sample in samples:
        members.setdefault(sample["image"], sample["_body"])
    _write_archive(destination, members, timestamp)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--only", choices=("training", "community"), default=None)
    parser.add_argument("--training-db", type=Path, default=None)
    parser.add_argument("--training-images", type=Path, default=None)
    parser.add_argument("--community-db", type=Path, default=None)
    parser.add_argument("--forum-media", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    training = community = None
    if arguments.only != "community":
        training = (
            arguments.training_db or training_store.TRAINING_DB_PATH,
            arguments.training_images or training_store.TRAINING_IMAGE_DIR,
        )
    if arguments.only != "training":
        try:
            from .. import community as community_core
            from .. import community_forum
        except ImportError:  # pragma: no cover - direct script execution
            import community as community_core
            import community_forum
        community = (
            arguments.community_db or Path(community_core.COMMUNITY_DB_PATH),
            arguments.forum_media or community_forum.media_dir(),
        )
    try:
        archive = export_bundle(arguments.output_dir, training=training, community=community)
    except ExportError as exc:
        raise SystemExit(f"export refused: {exc}") from exc
    print(archive)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
