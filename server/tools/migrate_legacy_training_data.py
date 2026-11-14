#!/usr/bin/env python3
"""Explicitly transform approved legacy feedback/labeling material.

This command never discovers default paths and never applies by default. A
separate approval JSON document must name each legacy row that passed the
operator's legal/privacy review. Joins through legacy scan IDs are held only in
memory while copying safe fields and are not represented in ``training.db``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from .. import training_store
except ImportError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import training_store


_CONSENT_VERSION = re.compile(r"[a-z0-9][a-z0-9_.:+-]{0,63}\Z")
_LEGACY_USER_SOURCES = frozenset({"user_scan", "user_feedback"})


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationPaths:
    labeling_database: Path
    labeling_images: Path
    feedback_database: Path
    feedback_images: Path
    approval_file: Path
    destination_database: Path
    destination_images: Path


def _read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise MigrationError("a legacy database does not exist")
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, isolation_level=None
    )
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    return connection


def _approved(path: Path) -> tuple[set[int], set[int]]:
    if not path.is_file():
        raise MigrationError("approval file does not exist")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError("approval file is invalid") from exc
    if not isinstance(document, dict) or set(document) != {
        "labeling_candidate_ids",
        "feedback_row_ids",
    }:
        raise MigrationError("approval file has an invalid shape")

    def identifiers(field: str) -> set[int]:
        values = document[field]
        if not isinstance(values, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise MigrationError("approval file contains an invalid row id")
        if len(values) != len(set(values)):
            raise MigrationError("approval file contains duplicate row ids")
        return set(values)

    return identifiers("labeling_candidate_ids"), identifiers("feedback_row_ids")


def _rows_by_ids(
    connection: sqlite3.Connection, table: str, identifiers: set[int]
) -> list[dict]:
    if not identifiers:
        return []
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if table not in table_names:
        raise MigrationError("a legacy database has an unexpected schema")
    placeholders = ",".join("?" for _ in identifiers)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id",
                tuple(sorted(identifiers)),
            )
        ]
    except sqlite3.DatabaseError as exc:
        raise MigrationError("a legacy database has an unexpected schema") from exc
    if {int(row["id"]) for row in rows} != identifiers:
        raise MigrationError("an approved legacy row does not exist")
    return rows


def _safe_legacy_image(directory: Path, value: object) -> Path:
    name = str(value or "")
    if not name or Path(name).name != name or name in {".", ".."}:
        raise MigrationError("an approved row has an unsafe image path")
    path = directory / name
    if not path.is_file():
        raise MigrationError("an approved legacy image does not exist")
    return path


def _list(value: object) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _top_k(value: object) -> list[dict]:
    result = []
    for item in _list(value):
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            result.append({"class": item[0], "confidence": item[1]})
    return result


def _created_day(*values: object) -> str:
    for value in values:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
    raise MigrationError("an approved row has no valid creation time")


def _status(value: object) -> tuple[str, str]:
    legacy = str(value or "pending").lower()
    mapping = {
        "yes": ("offline_yes", "offline_agent"),
        "no": ("offline_no", "offline_agent"),
        "not_sure": ("offline_unsure", "offline_agent"),
        "auto_yes": ("auto_yes", "model"),
        "pending": ("pending", "model"),
    }
    if legacy not in mapping:
        raise MigrationError("an approved row has an unknown review status")
    return mapping[legacy]


def _feedback_fields(feedback: dict | None) -> dict:
    if feedback is None:
        return {
            "correct": None,
            "corrected": None,
            "reason": None,
            "out_of_head": None,
            "provenance": None,
        }
    correct = bool(feedback.get("correct"))
    return {
        "correct": correct,
        "corrected": None if correct else feedback.get("corrected_class"),
        "reason": feedback.get("reason"),
        "out_of_head": feedback.get("out_of_head"),
        "provenance": "model+user",
    }


def _load_approved(paths: MigrationPaths) -> tuple[list[dict], list[dict]]:
    labeling_ids, feedback_ids = _approved(paths.approval_file)
    if not labeling_ids and not feedback_ids:
        raise MigrationError("approval file selects no rows")
    if not paths.labeling_images.is_dir() or not paths.feedback_images.is_dir():
        raise MigrationError("a legacy image directory does not exist")
    with contextlib.closing(_read_only(paths.labeling_database)) as labeling_connection:
        candidates = _rows_by_ids(labeling_connection, "candidates", labeling_ids)
    with contextlib.closing(_read_only(paths.feedback_database)) as feedback_connection:
        feedback_rows = _rows_by_ids(feedback_connection, "feedback", feedback_ids)
    return candidates, feedback_rows


def plan(paths: MigrationPaths) -> dict[str, int]:
    candidates, feedback_rows = _load_approved(paths)
    candidate_scan_ids = {
        str(row.get("scan_id"))
        for row in candidates
        if row.get("scan_id") is not None
    }
    joined = sum(
        1
        for row in feedback_rows
        if row.get("scan_id") is not None
        and str(row["scan_id"]) in candidate_scan_ids
    )
    standalone_feedback = sum(
        1
        for row in feedback_rows
        if row.get("image_path")
        and not bool(row.get("correct"))
        and (
            row.get("scan_id") is None
            or str(row["scan_id"]) not in candidate_scan_ids
        )
    )
    return {
        "approved_labeling_rows": len(candidates),
        "approved_feedback_rows": len(feedback_rows),
        "in_memory_joins": joined,
        "standalone_feedback_images": standalone_feedback,
        "potential_samples": len(candidates) + standalone_feedback,
    }


def _destination_is_fresh(paths: MigrationPaths) -> None:
    if paths.destination_database.exists() or paths.destination_images.exists():
        raise MigrationError("destination must not already exist")
    if paths.destination_database.resolve() in {
        paths.labeling_database.resolve(),
        paths.feedback_database.resolve(),
    }:
        raise MigrationError("destination must differ from legacy databases")
    if paths.destination_images.resolve() in {
        paths.labeling_images.resolve(),
        paths.feedback_images.resolve(),
    }:
        raise MigrationError("destination must differ from legacy image directories")


def _cleanup_new_destination(paths: MigrationPaths) -> None:
    with contextlib.suppress(OSError):
        if paths.destination_images.exists():
            shutil.rmtree(paths.destination_images)
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            Path(f"{paths.destination_database}{suffix}").unlink(missing_ok=True)


def apply(paths: MigrationPaths, *, consent_version: str) -> dict[str, int]:
    consent = consent_version.strip().lower()
    if _CONSENT_VERSION.fullmatch(consent) is None:
        raise MigrationError("consent version is invalid")
    _destination_is_fresh(paths)
    candidates, feedback_rows = _load_approved(paths)
    feedback_by_scan: dict[str, dict] = {}
    for row in feedback_rows:
        scan_id = row.get("scan_id")
        if scan_id is None:
            continue
        key = str(scan_id)
        current = feedback_by_scan.get(key)
        if current is None or int(row.get("created_at") or 0) >= int(
            current.get("created_at") or 0
        ):
            feedback_by_scan[key] = row

    old_database = training_store.TRAINING_DB_PATH
    old_images = training_store.TRAINING_IMAGE_DIR
    old_initialised = training_store._initialised
    created = duplicates = joined = standalone = 0
    used_feedback: set[int] = set()
    try:
        training_store.TRAINING_DB_PATH = paths.destination_database
        training_store.TRAINING_IMAGE_DIR = paths.destination_images
        training_store._initialised = False
        training_store.init_db()

        for candidate in candidates:
            source = str(candidate.get("source") or "")
            if source not in _LEGACY_USER_SOURCES:
                raise MigrationError("approved labeling row is not user-sourced")
            scan_id = candidate.get("scan_id")
            feedback = feedback_by_scan.get(str(scan_id)) if scan_id is not None else None
            if feedback is not None:
                used_feedback.add(int(feedback["id"]))
                joined += 1
            fields = _feedback_fields(feedback)
            review_status, provenance = _status(candidate.get("status"))
            # A completed offline verdict is the authoritative human review.
            # Preserve the structured user feedback as evidence, but never
            # demote or overwrite the reviewer provenance during the join.
            if fields["provenance"] and provenance != "offline_agent":
                provenance = str(fields["provenance"])
                if fields["correct"] is False:
                    review_status = "pending"
                elif fields["correct"] is True:
                    review_status = "auto_yes"
            image_path = _safe_legacy_image(
                paths.labeling_images, candidate.get("image_path")
            )
            sanitized = training_store.sanitize_training_image(image_path.read_bytes())
            predicted = candidate.get("model_pred") or candidate.get("class")
            corrected = fields["corrected"]
            stored_id, was_created = training_store.store_sample(
                sanitized,
                source=(
                    "feedback_upload"
                    if source == "user_feedback" or fields["correct"] is False
                    else "consented_scan"
                ),
                model_family="unknown",
                model_version=(feedback or {}).get("model_version") or "unknown",
                predicted_class=predicted,
                predicted_confidence=candidate.get("model_conf")
                if candidate.get("model_conf") is not None
                else (feedback or {}).get("predicted_confidence"),
                top_k=_top_k((feedback or {}).get("top3_json")),
                conformal_set=_list(
                    candidate.get("model_set")
                    or (feedback or {}).get("prediction_set_json")
                ),
                warnings=[],
                blur_bucket="unknown",
                detector_region_count=int((feedback or {}).get("region_count") or 0),
                bbox_area_buckets=[],
                severity="unknown",
                review_status=review_status,
                consent_version=consent,
                created_day=_created_day(
                    candidate.get("created_at"), (feedback or {}).get("created_at")
                ),
                user_correct=fields["correct"],
                corrected_class=corrected,
                correction_reason=fields["reason"],
                out_of_head=fields["out_of_head"],
                label_provenance=provenance,
            )
            del stored_id
            created += int(was_created)
            duplicates += int(not was_created)

        for feedback in feedback_rows:
            if int(feedback["id"]) in used_feedback:
                continue
            if bool(feedback.get("correct")) or not feedback.get("image_path"):
                continue
            image_path = _safe_legacy_image(
                paths.feedback_images, feedback.get("image_path")
            )
            sanitized = training_store.sanitize_training_image(image_path.read_bytes())
            _sample_id, was_created = training_store.store_sample(
                sanitized,
                source="feedback_upload",
                model_family="unknown",
                model_version=feedback.get("model_version") or "unknown",
                predicted_class=feedback.get("predicted_class"),
                predicted_confidence=feedback.get("predicted_confidence"),
                top_k=_top_k(feedback.get("top3_json")),
                conformal_set=_list(feedback.get("prediction_set_json")),
                warnings=[],
                blur_bucket="unknown",
                detector_region_count=int(feedback.get("region_count") or 0),
                bbox_area_buckets=[],
                severity="unknown",
                review_status="pending",
                consent_version=consent,
                created_day=_created_day(feedback.get("created_at")),
                user_correct=False,
                corrected_class=feedback.get("corrected_class"),
                correction_reason=feedback.get("reason"),
                out_of_head=feedback.get("out_of_head"),
                label_provenance="model+user",
            )
            created += int(was_created)
            duplicates += int(not was_created)
            standalone += 1

        paths.destination_database.chmod(0o600)
        for image in paths.destination_images.iterdir():
            if image.is_file():
                image.chmod(0o600)
        return {
            "created_samples": created,
            "deduplicated_samples": duplicates,
            "in_memory_joins": joined,
            "standalone_feedback_images": standalone,
        }
    except Exception:
        _cleanup_new_destination(paths)
        raise
    finally:
        training_store.TRAINING_DB_PATH = old_database
        training_store.TRAINING_IMAGE_DIR = old_images
        training_store._initialised = old_initialised


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-labeling-db", required=True, type=Path)
    parser.add_argument("--legacy-labeling-images", required=True, type=Path)
    parser.add_argument("--legacy-feedback-db", required=True, type=Path)
    parser.add_argument("--legacy-feedback-images", required=True, type=Path)
    parser.add_argument("--approved-ids", required=True, type=Path)
    parser.add_argument("--destination-db", required=True, type=Path)
    parser.add_argument("--destination-images", required=True, type=Path)
    parser.add_argument("--consent-version", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration; without this flag only a read-only plan runs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    paths = MigrationPaths(
        labeling_database=arguments.legacy_labeling_db,
        labeling_images=arguments.legacy_labeling_images,
        feedback_database=arguments.legacy_feedback_db,
        feedback_images=arguments.legacy_feedback_images,
        approval_file=arguments.approved_ids,
        destination_database=arguments.destination_db,
        destination_images=arguments.destination_images,
    )
    try:
        report = (
            apply(paths, consent_version=arguments.consent_version)
            if arguments.apply
            else plan(paths)
        )
    except (MigrationError, training_store.TrainingStoreError, OSError) as exc:
        raise SystemExit(f"migration refused: {exc}") from exc
    print(json.dumps({"mode": "apply" if arguments.apply else "dry_run", **report}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
