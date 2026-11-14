#!/usr/bin/env python3
"""Export the de-identified training store into a privacy-checked archive.

The serving database is opened through SQLite's read-only URI. Every image is
hash-verified and inspected for EXIF/XMP/IPTC/ICC metadata before anything is
written. Near-duplicate groups are assigned in the manifest so downstream
preparation can lock a whole group to one train/evaluation/calibration split.
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
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

try:
    from .. import training_store
except ImportError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import training_store


NEAR_DUPLICATE_DISTANCE = 8
_PHASH = re.compile(r"[0-9a-f]{16}\Z")
_IMAGE_KEY = re.compile(r"[0-9a-f-]{36}\.jpg\Z")

_MANIFEST_COLUMNS = (
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
    """Raised when the source cannot satisfy the export privacy contract."""


def _read_only_connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise ExportError("training database does not exist")
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro",
        uri=True,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _safe_json_list(raw: str, field: str) -> list:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ExportError(f"invalid structured field: {field}") from exc
    if not isinstance(value, list):
        raise ExportError(f"invalid structured field: {field}")
    return value


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


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

    def query(self, value: str, distance_limit: int) -> Iterable[int]:
        distance = _hamming(value, self.value)
        if distance <= distance_limit:
            yield self.index
        lower = distance - distance_limit
        upper = distance + distance_limit
        for edge, child in self.children.items():
            if lower <= edge <= upper:
                yield from child.query(value, distance_limit)


def _duplicate_groups(rows: list[dict]) -> tuple[dict[str, str], dict]:
    union = _UnionFind(len(rows))
    tree: _BKNode | None = None
    exact: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        phash = str(row["perceptual_hash"])
        if _PHASH.fullmatch(phash) is None:
            raise ExportError("invalid perceptual hash")
        exact[str(row["sha256"])].append(index)
        if tree is None:
            tree = _BKNode(phash, index)
        else:
            for match in tree.query(phash, NEAR_DUPLICATE_DISTANCE):
                union.union(index, match)
            tree.add(phash, index)

    for indexes in exact.values():
        for index in indexes[1:]:
            union.union(indexes[0], index)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        components[union.find(index)].append(index)

    assignments: dict[str, str] = {}
    near_groups: list[dict] = []
    for indexes in components.values():
        members = sorted(str(rows[index]["sample_id"]) for index in indexes)
        digest = hashlib.sha256("\n".join(members).encode("ascii")).hexdigest()[:16]
        group_id = f"group-{digest}"
        for sample_id in members:
            assignments[sample_id] = group_id
        if len(indexes) > 1:
            near_groups.append(
                {
                    "duplicate_group": group_id,
                    "sample_ids": members,
                    "phashes": sorted(
                        {str(rows[index]["perceptual_hash"]) for index in indexes}
                    ),
                }
            )

    exact_groups = [
        {
            "sha256": digest,
            "sample_ids": sorted(str(rows[index]["sample_id"]) for index in indexes),
        }
        for digest, indexes in exact.items()
        if len(indexes) > 1
    ]
    return assignments, {
        "near_duplicate_hamming_distance": NEAR_DUPLICATE_DISTANCE,
        "exact_groups": sorted(exact_groups, key=lambda item: item["sha256"]),
        "near_groups": sorted(
            near_groups, key=lambda item: item["duplicate_group"]
        ),
    }


def _inventory(rows: list[dict]) -> dict:
    def counts(key: str) -> dict[str, int]:
        values = Counter(str(row.get(key) or "unknown") for row in rows)
        return dict(sorted(values.items()))

    combinations = Counter(
        (
            str(row.get("predicted_class") or row.get("corrected_class") or "unknown"),
            str(row["source"]),
            str(row["model_version"]),
        )
        for row in rows
    )
    return {
        "total": len(rows),
        "by_class": counts("predicted_class"),
        "by_source": counts("source"),
        "by_model_version": counts("model_version"),
        "by_review_status": counts("review_status"),
        "class_source_model": [
            {
                "class": class_name,
                "source": source,
                "model_version": model_version,
                "count": count,
            }
            for (class_name, source, model_version), count in sorted(
                combinations.items()
            )
        ],
    }


def _usage_for(row: dict) -> str:
    if row["review_status"] == "offline_yes":
        return "evaluation_eligible"
    if row["review_status"] in {"offline_no", "offline_unsure", "pending"}:
        return "review_required"
    return "train_only"


def _manifest_row(row: dict, duplicate_group: str) -> dict:
    return {
        "sample_id": row["sample_id"],
        "image": f"images/{row['image_key']}",
        "sha256": row["sha256"],
        "perceptual_hash": row["perceptual_hash"],
        "duplicate_group": duplicate_group,
        "created_day": row["created_day"],
        "source": row["source"],
        "model_family": row["model_family"],
        "model_version": row["model_version"],
        "predicted_class": row["predicted_class"],
        "predicted_confidence": row["predicted_confidence"],
        "top_k": _safe_json_list(row["top_k_json"], "top_k"),
        "conformal_set": _safe_json_list(
            row["conformal_set_json"], "conformal_set"
        ),
        "warnings": _safe_json_list(row["warnings_json"], "warnings"),
        "blur_bucket": row["blur_bucket"],
        "detector_region_count": row["detector_region_count"],
        "bbox_area_buckets": _safe_json_list(
            row["bbox_area_buckets_json"], "bbox_area_buckets"
        ),
        "severity": row["severity"],
        "user_correct": (
            None if row["user_correct"] is None else bool(row["user_correct"])
        ),
        "corrected_class": row["corrected_class"],
        "correction_reason": row["correction_reason"],
        "out_of_head": (
            None if row["out_of_head"] is None else bool(row["out_of_head"])
        ),
        "review_status": row["review_status"],
        "label_provenance": row["label_provenance"],
        "consent_version": row["consent_version"],
        "offline_usage": _usage_for(row),
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _tar_info(name: str, body: bytes, timestamp: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = 0o444
    info.mtime = timestamp
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def export_training_data(
    database: Path,
    image_directory: Path,
    output_directory: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Create one timestamped, read-only archive or fail without an archive."""
    database = Path(database)
    image_directory = Path(image_directory)
    output_directory = Path(output_directory)
    if not image_directory.is_dir():
        raise ExportError("training image directory does not exist")

    generated = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    timestamp = int(generated.timestamp())
    archive_name = f"training-export-{generated.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / archive_name
    if destination.exists():
        raise ExportError("export archive already exists")

    columns = ", ".join(_MANIFEST_COLUMNS)
    with closing(_read_only_connection(database)) as connection:
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

    assignments, duplicates = _duplicate_groups(rows)
    manifest = [
        _manifest_row(row, assignments[str(row["sample_id"])]) for row in rows
    ]
    text_members: dict[str, bytes] = {
        "metadata.json": _json_bytes(
            {
                "schema_version": training_store.SCHEMA_VERSION,
                "export_version": training_store.EXPORT_VERSION,
                "generated_at": generated.isoformat().replace("+00:00", "Z"),
                "sample_count": len(rows),
                "split_policy": "duplicate_group_locked",
                "auto_yes_policy": "train_only_until_offline_verification",
            }
        ),
        "manifest.jsonl": b"".join(
            json.dumps(item, ensure_ascii=True, sort_keys=True).encode("utf-8") + b"\n"
            for item in manifest
        ),
        "inventory.json": _json_bytes(_inventory(rows)),
        "duplicates.json": _json_bytes(duplicates),
    }

    image_members: dict[str, bytes] = {}
    for row in rows:
        image_key = str(row["image_key"])
        if _IMAGE_KEY.fullmatch(image_key) is None or Path(image_key).name != image_key:
            raise ExportError("unsafe training image key")
        source = image_directory / image_key
        try:
            body = source.read_bytes()
        except OSError as exc:
            raise ExportError("training image is missing") from exc
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ExportError("training image checksum mismatch")
        if not training_store.metadata_is_absent(body):
            raise ExportError("training image contains metadata")
        image_members[f"images/{image_key}"] = body

    checksums = {
        name: hashlib.sha256(body).hexdigest()
        for name, body in {**text_members, **image_members}.items()
    }
    checksum_body = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(checksums.items())
    ).encode("ascii")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_directory,
            prefix=f".{archive_name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        with tarfile.open(temporary_path, "w:gz", compresslevel=9) as archive:
            for name, body in sorted({**text_members, **image_members}.items()):
                archive.addfile(_tar_info(name, body, timestamp), io.BytesIO(body))
            archive.addfile(
                _tar_info("checksums.sha256", checksum_body, timestamp),
                io.BytesIO(checksum_body),
            )
        temporary_path.chmod(0o400)
        os.replace(temporary_path, destination)
        temporary_path = None
        destination.chmod(0o444)
        return destination
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="training.db path")
    parser.add_argument(
        "--images", type=Path, required=True, help="sanitized training image directory"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="archive destination directory"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        archive = export_training_data(
            arguments.db, arguments.images, arguments.output_dir
        )
    except ExportError as exc:
        raise SystemExit(f"export refused: {exc}") from exc
    print(archive)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
