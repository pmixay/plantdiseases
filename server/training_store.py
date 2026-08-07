"""De-identified, consent-gated training sample and feedback receipt store.

The store deliberately has no account, community actor, installation, IP,
client scan, device, filename, URL, or free-text columns.  A random 256-bit
receipt can briefly connect a feedback action to an anonymous sample; only its
SHA-256 digest is stored and receipts expire after 30 days.

Images accepted here are detector-backed leaf crops or explicitly migrated
images that have been decoded, orientation-normalized, resized, and re-encoded
as fresh RGB JPEGs.  They remain described as *de-identified*, not anonymous,
until the intended privacy review has been completed.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

try:
    from .config import get_str
except ImportError:  # pragma: no cover - script-mode tools
    from config import get_str


SCHEMA_VERSION = "1"
EXPORT_VERSION = "1"
RECEIPT_TTL_SECONDS = 30 * 86_400
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_DIMENSION = 8_192
TRAINING_IMAGE_MAX_SIDE = 1_024

_MODULE_DIR = Path(__file__).resolve().parent
TRAINING_DB_PATH = Path(
    get_str("TRAINING_DB_PATH", str(_MODULE_DIR / "training.db"))
)
TRAINING_IMAGE_DIR = Path(
    get_str("TRAINING_IMAGE_DIR", str(_MODULE_DIR / "training_images"))
)

_SOURCE_VALUES = frozenset({"consented_scan", "feedback_upload"})
_REVIEW_VALUES = frozenset(
    {"pending", "auto_yes", "offline_yes", "offline_no", "offline_unsure"}
)
_PROVENANCE_VALUES = frozenset({"model", "model+user", "offline_agent"})
_RECEIPT_STATES = frozenset(
    {"pending", "available", "no_sample", "updated", "consumed"}
)
_SEVERITY_VALUES = frozenset({"unknown", "low", "medium", "high"})
_BLUR_VALUES = frozenset({"unknown", "blurry", "borderline", "sharp"})
_AREA_VALUES = frozenset({"tiny", "small", "medium", "large"})
_STRUCTURED_VALUE = re.compile(r"[a-z0-9][a-z0-9_.:+-]{0,63}\Z")
_RECEIPT_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS training_samples (
    sample_id                  TEXT PRIMARY KEY,
    image_key                  TEXT NOT NULL UNIQUE,
    sha256                     TEXT NOT NULL UNIQUE,
    perceptual_hash            TEXT NOT NULL,
    created_day                TEXT NOT NULL,
    source                     TEXT NOT NULL CHECK(source IN ('consented_scan', 'feedback_upload')),
    model_family               TEXT NOT NULL,
    model_version              TEXT NOT NULL,
    predicted_class            TEXT,
    predicted_confidence       REAL CHECK(predicted_confidence IS NULL OR
                                          (predicted_confidence >= 0 AND predicted_confidence <= 1)),
    top_k_json                 TEXT NOT NULL DEFAULT '[]',
    conformal_set_json         TEXT NOT NULL DEFAULT '[]',
    warnings_json              TEXT NOT NULL DEFAULT '[]',
    blur_bucket                TEXT NOT NULL CHECK(blur_bucket IN
                                      ('unknown', 'blurry', 'borderline', 'sharp')),
    detector_region_count      INTEGER NOT NULL CHECK(detector_region_count >= 0 AND
                                                      detector_region_count <= 64),
    bbox_area_buckets_json     TEXT NOT NULL DEFAULT '[]',
    severity                   TEXT NOT NULL CHECK(severity IN
                                      ('unknown', 'low', 'medium', 'high')),
    user_correct               INTEGER CHECK(user_correct IS NULL OR user_correct IN (0, 1)),
    corrected_class            TEXT,
    correction_reason          TEXT,
    out_of_head                INTEGER CHECK(out_of_head IS NULL OR out_of_head IN (0, 1)),
    review_status              TEXT NOT NULL CHECK(review_status IN
                                      ('pending', 'auto_yes', 'offline_yes',
                                       'offline_no', 'offline_unsure')),
    label_provenance           TEXT NOT NULL CHECK(label_provenance IN
                                      ('model', 'model+user', 'offline_agent')),
    consent_version            TEXT NOT NULL,
    export_status              TEXT NOT NULL DEFAULT 'pending'
                                      CHECK(export_status IN ('pending', 'exported')),
    prune_status               TEXT NOT NULL DEFAULT 'retained'
                                      CHECK(prune_status IN ('retained', 'eligible'))
);
CREATE INDEX IF NOT EXISTS ix_training_inventory
    ON training_samples(predicted_class, source, model_version);
CREATE INDEX IF NOT EXISTS ix_training_created_day
    ON training_samples(created_day, review_status);
CREATE INDEX IF NOT EXISTS ix_training_phash
    ON training_samples(perceptual_hash);

CREATE TABLE IF NOT EXISTS feedback_receipts (
    receipt_hash BLOB PRIMARY KEY CHECK(length(receipt_hash) = 32),
    sample_id    TEXT REFERENCES training_samples(sample_id) ON DELETE SET NULL,
    expires_at   INTEGER NOT NULL,
    state        TEXT NOT NULL CHECK(state IN
                     ('pending', 'available', 'no_sample', 'updated', 'consumed'))
);
CREATE INDEX IF NOT EXISTS ix_feedback_receipts_expiry
    ON feedback_receipts(expires_at);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


class TrainingStoreError(ValueError):
    """A bounded, non-reflective training-store contract error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SanitizedTrainingImage:
    body: bytes
    sha256: str
    perceptual_hash: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    updated: bool
    stored_image: bool = False


def _connect(*, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{TRAINING_DB_PATH.resolve()}?mode=ro"
        connection = sqlite3.connect(
            uri, uri=True, check_same_thread=False, isolation_level=None
        )
    else:
        connection = sqlite3.connect(
            str(TRAINING_DB_PATH), check_same_thread=False, isolation_level=None
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    global _initialised
    with _init_lock:
        if _initialised:
            return
        TRAINING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRAINING_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _connect() as connection:
            connection.executescript(_SCHEMA)
        _initialised = True


def _day(value: int | float | datetime | None = None) -> str:
    if isinstance(value, datetime):
        moment = value.astimezone(UTC)
    else:
        moment = datetime.fromtimestamp(
            time.time() if value is None else float(value), tz=UTC
        )
    return moment.date().isoformat()


def _receipt_digest(receipt: str) -> bytes:
    if not isinstance(receipt, str) or _RECEIPT_PATTERN.fullmatch(receipt) is None:
        raise TrainingStoreError("feedback_receipt_invalid")
    return hashlib.sha256(receipt.encode("ascii")).digest()


def issue_feedback_receipt(
    *, capture_pending: bool, now: int | None = None
) -> str:
    """Create a one-use-capable random receipt and persist only its digest."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    state = "pending" if capture_pending else "no_sample"
    with _write_lock, _connect() as connection:
        for _attempt in range(4):
            receipt = secrets.token_urlsafe(32)
            digest = hashlib.sha256(receipt.encode("ascii")).digest()
            try:
                connection.execute(
                    "INSERT INTO feedback_receipts "
                    "(receipt_hash, sample_id, expires_at, state) VALUES (?, NULL, ?, ?)",
                    (digest, timestamp + RECEIPT_TTL_SECONDS, state),
                )
                return receipt
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("unable to allocate feedback receipt")


def _structured(value: object, *, default: str = "unknown") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _STRUCTURED_VALUE.fullmatch(candidate) else default


def _structured_list(values: Iterable[object] | None, *, limit: int = 16) -> list[str]:
    result: list[str] = []
    for value in list(values or [])[:limit]:
        candidate = _structured(value, default="")
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _top_k(values: Sequence[dict] | Sequence[tuple[str, float]] | None) -> list[dict]:
    cleaned: list[dict] = []
    for entry in list(values or [])[:10]:
        try:
            if isinstance(entry, dict):
                label = entry.get("class")
                score = entry.get("confidence", entry.get("score", entry.get("prob")))
            else:
                label, score = entry
            class_name = _structured(label, default="")
            probability = float(score)
            if not class_name or not math.isfinite(probability):
                continue
            cleaned.append(
                {"class": class_name, "score": round(max(0.0, min(1.0, probability)), 6)}
            )
        except (TypeError, ValueError):
            continue
    return cleaned


def _perceptual_hash(image: Image.Image) -> str:
    """Return a deterministic 64-bit DCT perceptual hash without extra deps."""
    pixels = np.asarray(
        image.convert("L").resize((32, 32), Image.Resampling.LANCZOS),
        dtype=np.float64,
    )
    positions = np.arange(32, dtype=np.float64)
    frequencies = np.arange(8, dtype=np.float64)[:, None]
    basis = np.cos((math.pi / 32.0) * (positions + 0.5) * frequencies)
    coefficients = basis @ pixels @ basis.T
    low = coefficients[:8, :8].reshape(-1)
    threshold = float(np.median(low[1:]))
    bits = 0
    for value in low:
        bits = (bits << 1) | int(value > threshold)
    return f"{bits:016x}"


def sanitize_training_image(
    source: bytes | Image.Image,
    *,
    max_side: int = TRAINING_IMAGE_MAX_SIDE,
) -> SanitizedTrainingImage:
    """Decode/orient/resize/re-encode a fresh metadata-free RGB JPEG."""
    try:
        if isinstance(source, bytes):
            if not source or len(source) > MAX_SOURCE_IMAGE_BYTES:
                raise TrainingStoreError("training_image_invalid")
            opened = Image.open(io.BytesIO(source))
            if max(opened.size) > MAX_SOURCE_DIMENSION:
                raise TrainingStoreError("training_image_invalid")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        elif isinstance(source, Image.Image):
            if max(source.size) > MAX_SOURCE_DIMENSION:
                raise TrainingStoreError("training_image_invalid")
            image = ImageOps.exif_transpose(source).convert("RGB")
        else:
            raise TrainingStoreError("training_image_invalid")
    except TrainingStoreError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        raise TrainingStoreError("training_image_invalid") from None

    if min(image.size) < 32:
        raise TrainingStoreError("training_image_invalid")
    bounded_side = max(64, min(int(max_side), TRAINING_IMAGE_MAX_SIDE))
    if max(image.size) > bounded_side:
        image.thumbnail((bounded_side, bounded_side), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    buffer = io.BytesIO()
    clean.save(
        buffer,
        format="JPEG",
        quality=88,
        optimize=True,
        progressive=False,
        subsampling=2,
    )
    body = buffer.getvalue()
    return SanitizedTrainingImage(
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        perceptual_hash=_perceptual_hash(clean),
        width=clean.width,
        height=clean.height,
    )


def metadata_is_absent(body: bytes) -> bool:
    """Verify that an exported image carries no common metadata containers."""
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.load()
            forbidden_info = {"exif", "xmp", "XML:com.adobe.xmp", "icc_profile", "iptc"}
            return (
                image.format == "JPEG"
                and not image.getexif()
                and forbidden_info.isdisjoint(image.info)
            )
    except (OSError, UnidentifiedImageError):
        return False


def _area_bucket(fraction: float) -> str:
    if fraction < 0.02:
        return "tiny"
    if fraction < 0.10:
        return "small"
    if fraction < 0.35:
        return "medium"
    return "large"


def extract_safe_leaf_crop(
    image: Image.Image,
    detection: dict | None,
) -> tuple[Image.Image, list[str]] | None:
    """Return a bounded detector-backed leaf crop; never fall back to full frame."""
    regions = (detection or {}).get("regions")
    if not isinstance(regions, list) or not regions:
        return None
    width, height = image.size
    image_area = max(width * height, 1)
    valid: list[tuple[int, int, int, int, float]] = []
    for region in regions[:64]:
        if not isinstance(region, dict):
            continue
        try:
            left = max(0, int(region["x"]))
            top = max(0, int(region["y"]))
            right = min(width, left + int(region["width"]))
            bottom = min(height, top + int(region["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        if right - left < 32 or bottom - top < 32:
            continue
        fraction = ((right - left) * (bottom - top)) / image_area
        if fraction < 0.002:
            continue
        valid.append((left, top, right, bottom, fraction))
    if not valid:
        return None
    valid.sort(key=lambda value: value[4], reverse=True)
    left, top, right, bottom, _fraction = valid[0]
    return image.crop((left, top, right, bottom)), [
        _area_bucket(value[4]) for value in valid
    ]


def blur_score_bucket(image: Image.Image) -> str:
    """Coarsely bucket edge variance; never retain a precise fingerprint."""
    preview = image.convert("L")
    if max(preview.size) > 256:
        preview.thumbnail((256, 256), Image.Resampling.BILINEAR)
    edges = np.asarray(preview.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    score = float(edges.var())
    if score < 80:
        return "blurry"
    if score < 250:
        return "borderline"
    return "sharp"


def route_model_review(
    predicted_class: str,
    confidence: float | None,
    conformal_classes: Sequence[str] | None,
    *,
    trust_conformal: bool = True,
    confidence_threshold: float = 0.85,
) -> str:
    classes = _structured_list(conformal_classes)
    predicted = _structured(predicted_class, default="")
    if trust_conformal and classes:
        return "auto_yes" if classes == [predicted] else "pending"
    return "auto_yes" if float(confidence or 0.0) >= confidence_threshold else "pending"


def deterministically_sample(sha256: str, rate: float) -> bool:
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return False
    return int(sha256[:8], 16) % 10_000 < int(rate * 10_000)


def _receipt_row(
    connection: sqlite3.Connection,
    receipt: str,
    *,
    now: int,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT receipt_hash, sample_id, expires_at, state FROM feedback_receipts "
        "WHERE receipt_hash = ?",
        (_receipt_digest(receipt),),
    ).fetchone()
    if row is None or int(row["expires_at"]) <= now:
        raise TrainingStoreError("feedback_receipt_invalid")
    if row["state"] == "consumed":
        raise TrainingStoreError("feedback_receipt_used")
    return row


def _write_image(image_key: str, body: bytes) -> Path:
    TRAINING_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = TRAINING_IMAGE_DIR / image_key
    temporary = TRAINING_IMAGE_DIR / f".{image_key}.tmp"
    temporary.write_bytes(body)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def store_sample(
    image: SanitizedTrainingImage,
    *,
    source: str,
    model_family: str,
    model_version: str,
    predicted_class: str | None,
    predicted_confidence: float | None,
    top_k: Sequence[dict] | Sequence[tuple[str, float]] | None,
    conformal_set: Sequence[str] | None,
    warnings: Sequence[str] | None,
    blur_bucket: str,
    detector_region_count: int,
    bbox_area_buckets: Sequence[str] | None,
    severity: str,
    review_status: str,
    consent_version: str,
    receipt: str | None = None,
    created_day: str | None = None,
    user_correct: bool | None = None,
    corrected_class: str | None = None,
    correction_reason: str | None = None,
    out_of_head: bool | None = None,
    label_provenance: str = "model",
) -> tuple[str, bool]:
    """Store one sanitized image, idempotent by post-sanitization SHA-256."""
    init_db()
    if source not in _SOURCE_VALUES:
        raise TrainingStoreError("training_source_invalid")
    if review_status not in _REVIEW_VALUES:
        raise TrainingStoreError("training_status_invalid")
    if label_provenance not in _PROVENANCE_VALUES:
        raise TrainingStoreError("training_provenance_invalid")
    blur = blur_bucket if blur_bucket in _BLUR_VALUES else "unknown"
    severity_value = severity if severity in _SEVERITY_VALUES else "unknown"
    consent = _structured(consent_version, default="")
    if not consent:
        raise TrainingStoreError("training_consent_invalid")
    predicted = _structured(predicted_class, default="") or None
    corrected = _structured(corrected_class, default="") or None
    reason = _structured(correction_reason, default="") or None
    family = _structured(model_family)
    version = _structured(model_version)
    warning_values = _structured_list(warnings)
    conformal_values = _structured_list(conformal_set)
    area_values = [value for value in list(bbox_area_buckets or [])[:64] if value in _AREA_VALUES]
    confidence = None
    if predicted_confidence is not None:
        confidence = max(0.0, min(1.0, float(predicted_confidence)))
    regions = max(0, min(64, int(detector_region_count)))
    day = created_day or _day()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None:
        raise TrainingStoreError("training_day_invalid")

    timestamp = int(time.time())
    target: Path | None = None
    with _write_lock, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT sample_id FROM training_samples WHERE sha256 = ?",
                (image.sha256,),
            ).fetchone()
            if existing is not None:
                sample_id = str(existing["sample_id"])
                created = False
            else:
                sample_id = str(uuid.uuid4())
                image_key = f"{sample_id}.jpg"
                target = _write_image(image_key, image.body)
                connection.execute(
                    "INSERT INTO training_samples ("
                    "sample_id, image_key, sha256, perceptual_hash, created_day, source, "
                    "model_family, model_version, predicted_class, predicted_confidence, "
                    "top_k_json, conformal_set_json, warnings_json, blur_bucket, "
                    "detector_region_count, bbox_area_buckets_json, severity, user_correct, "
                    "corrected_class, correction_reason, out_of_head, review_status, "
                    "label_provenance, consent_version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sample_id,
                        image_key,
                        image.sha256,
                        image.perceptual_hash,
                        day,
                        source,
                        family,
                        version,
                        predicted,
                        confidence,
                        json.dumps(_top_k(top_k), separators=(",", ":")),
                        json.dumps(conformal_values, separators=(",", ":")),
                        json.dumps(warning_values, separators=(",", ":")),
                        blur,
                        regions,
                        json.dumps(area_values, separators=(",", ":")),
                        severity_value,
                        None if user_correct is None else int(user_correct),
                        corrected,
                        reason,
                        None if out_of_head is None else int(out_of_head),
                        review_status,
                        label_provenance,
                        consent,
                    ),
                )
                created = True
            if receipt is not None:
                receipt_row = _receipt_row(connection, receipt, now=timestamp)
                if receipt_row["state"] != "pending":
                    raise TrainingStoreError("feedback_receipt_invalid")
                connection.execute(
                    "UPDATE feedback_receipts SET sample_id = ?, state = 'available' "
                    "WHERE receipt_hash = ?",
                    (sample_id, receipt_row["receipt_hash"]),
                )
            connection.execute("COMMIT")
            return sample_id, created
        except Exception:
            connection.execute("ROLLBACK")
            if target is not None:
                target.unlink(missing_ok=True)
            raise


def complete_receipt_without_sample(receipt: str, *, now: int | None = None) -> None:
    init_db()
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as connection:
        row = _receipt_row(connection, receipt, now=timestamp)
        if row["state"] == "pending":
            connection.execute(
                "UPDATE feedback_receipts SET state = 'no_sample' WHERE receipt_hash = ?",
                (row["receipt_hash"],),
            )


def _apply_feedback_to_sample(
    connection: sqlite3.Connection,
    sample_id: str,
    *,
    correct: bool,
    corrected_class: str | None,
    correction_reason: str | None,
    out_of_head: bool | None,
) -> None:
    corrected = _structured(corrected_class, default="") or None
    reason = _structured(correction_reason, default="") or None
    if correct:
        connection.execute(
            "UPDATE training_samples SET user_correct = 1, corrected_class = NULL, "
            "correction_reason = ?, out_of_head = NULL, label_provenance = 'model+user', "
            "review_status = CASE WHEN predicted_class IS NOT NULL THEN 'auto_yes' "
            "                         ELSE review_status END "
            "WHERE sample_id = ?",
            (reason, sample_id),
        )
    else:
        connection.execute(
            "UPDATE training_samples SET user_correct = 0, corrected_class = ?, "
            "correction_reason = ?, out_of_head = ?, label_provenance = 'model+user', "
            "review_status = 'pending' WHERE sample_id = ?",
            (
                corrected,
                reason,
                None if out_of_head is None else int(out_of_head),
                sample_id,
            ),
        )


def apply_feedback(
    receipt: str,
    *,
    correct: bool,
    corrected_class: str | None = None,
    correction_reason: str | None = None,
    out_of_head: bool | None = None,
    now: int | None = None,
) -> FeedbackResult:
    init_db()
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = _receipt_row(connection, receipt, now=timestamp)
            if row["state"] == "pending":
                raise TrainingStoreError("feedback_not_ready")
            if row["state"] == "updated":
                raise TrainingStoreError("feedback_receipt_used")
            sample_id = row["sample_id"]
            if sample_id is not None:
                _apply_feedback_to_sample(
                    connection,
                    str(sample_id),
                    correct=correct,
                    corrected_class=corrected_class,
                    correction_reason=correction_reason,
                    out_of_head=out_of_head,
                )
            connection.execute(
                "UPDATE feedback_receipts SET state = 'updated' WHERE receipt_hash = ?",
                (row["receipt_hash"],),
            )
            connection.execute("COMMIT")
            return FeedbackResult(updated=sample_id is not None)
        except Exception:
            connection.execute("ROLLBACK")
            raise


def store_feedback_upload(
    receipt: str,
    image: SanitizedTrainingImage,
    *,
    correct: bool,
    corrected_class: str | None,
    correction_reason: str | None,
    out_of_head: bool | None,
    consent_version: str,
    now: int | None = None,
) -> FeedbackResult:
    """Consume a receipt and retain only useful, privacy-screened feedback pixels."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    target: Path | None = None
    with _write_lock, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = _receipt_row(connection, receipt, now=timestamp)
            if row["state"] == "pending":
                raise TrainingStoreError("feedback_not_ready")
            sample_id = str(row["sample_id"]) if row["sample_id"] is not None else None
            stored_image = False
            if sample_id is not None:
                _apply_feedback_to_sample(
                    connection,
                    sample_id,
                    correct=correct,
                    corrected_class=corrected_class,
                    correction_reason=correction_reason,
                    out_of_head=out_of_head,
                )
            elif not correct:
                existing = connection.execute(
                    "SELECT sample_id FROM training_samples WHERE sha256 = ?",
                    (image.sha256,),
                ).fetchone()
                if existing is not None:
                    sample_id = str(existing["sample_id"])
                else:
                    sample_id = str(uuid.uuid4())
                    image_key = f"{sample_id}.jpg"
                    target = _write_image(image_key, image.body)
                    corrected = _structured(corrected_class, default="") or None
                    reason = _structured(correction_reason, default="") or None
                    consent = _structured(consent_version, default="")
                    if not consent:
                        raise TrainingStoreError("training_consent_invalid")
                    connection.execute(
                        "INSERT INTO training_samples ("
                        "sample_id, image_key, sha256, perceptual_hash, created_day, source, "
                        "model_family, model_version, predicted_class, predicted_confidence, "
                        "blur_bucket, detector_region_count, bbox_area_buckets_json, severity, "
                        "user_correct, corrected_class, correction_reason, out_of_head, "
                        "review_status, label_provenance, consent_version) "
                        "VALUES (?, ?, ?, ?, ?, 'feedback_upload', 'unknown', 'unknown', "
                        "NULL, NULL, 'unknown', 0, '[]', 'unknown', 0, ?, ?, ?, "
                        "'pending', 'model+user', ?)",
                        (
                            sample_id,
                            image_key,
                            image.sha256,
                            image.perceptual_hash,
                            _day(timestamp),
                            corrected,
                            reason,
                            None if out_of_head is None else int(out_of_head),
                            consent,
                        ),
                    )
                    stored_image = True
                _apply_feedback_to_sample(
                    connection,
                    sample_id,
                    correct=False,
                    corrected_class=corrected_class,
                    correction_reason=correction_reason,
                    out_of_head=out_of_head,
                )
            connection.execute(
                "UPDATE feedback_receipts SET sample_id = ?, state = 'consumed' "
                "WHERE receipt_hash = ?",
                (sample_id, row["receipt_hash"]),
            )
            connection.execute("COMMIT")
            return FeedbackResult(updated=sample_id is not None, stored_image=stored_image)
        except Exception:
            connection.execute("ROLLBACK")
            if target is not None:
                target.unlink(missing_ok=True)
            raise


def count_samples_for_day(predicted_class: str, *, day: str | None = None) -> int:
    init_db()
    class_name = _structured(predicted_class, default="")
    if not class_name:
        return 0
    with _connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM training_samples "
            "WHERE source = 'consented_scan' AND predicted_class = ? AND created_day = ?",
            (class_name, day or _day()),
        ).fetchone()
    return int(row["n"])


def sample_by_receipt(receipt: str, *, now: int | None = None) -> dict | None:
    """Test/internal lookup; callers must never serialize the returned row."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    with _connect(read_only=True) as connection:
        row = _receipt_row(connection, receipt, now=timestamp)
        if row["sample_id"] is None:
            return None
        sample = connection.execute(
            "SELECT * FROM training_samples WHERE sample_id = ?",
            (row["sample_id"],),
        ).fetchone()
    return dict(sample) if sample is not None else None


def training_counts() -> dict:
    init_db()
    with _connect(read_only=True) as connection:
        total = int(connection.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0])
        sources = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT source, COUNT(*) FROM training_samples GROUP BY source"
            )
        }
        statuses = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT review_status, COUNT(*) FROM training_samples GROUP BY review_status"
            )
        }
    return {"total": total, "by_source": sources, "by_review_status": statuses}


def prune_training_data(
    retention_days: int,
    max_images: int,
    *,
    now: int | None = None,
) -> dict[str, int]:
    """Prune expired receipts and bounded low-value samples with their images."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    cutoff_day = _day(timestamp - max(1, int(retention_days)) * 86_400)
    bounded_max = max(0, int(max_images))
    selected: list[sqlite3.Row] = []
    with _write_lock, _connect() as connection:
        expired_receipts = int(
            connection.execute(
                "DELETE FROM feedback_receipts WHERE expires_at <= ?", (timestamp,)
            ).rowcount
            or 0
        )
        old = connection.execute(
            "SELECT sample_id, image_key FROM training_samples "
            "WHERE created_day < ? AND (user_correct IS NULL OR user_correct = 1) "
            "ORDER BY created_day",
            (cutoff_day,),
        ).fetchall()
        total = int(connection.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0])
        # Include work left in the eligible phase by an interrupted prior run.
        selected = list(
            connection.execute(
                "SELECT sample_id, image_key FROM training_samples "
                "WHERE prune_status = 'eligible'"
            ).fetchall()
        )
        eligible_ids = {str(item["sample_id"]) for item in selected}
        selected.extend(
            row
            for row in old
            if str(row["sample_id"]) not in eligible_ids
        )
        selected_ids = {str(row["sample_id"]) for row in selected}
        remaining = total - len(selected)
        if remaining > bounded_max:
            extra = connection.execute(
                "SELECT sample_id, image_key FROM training_samples "
                "WHERE review_status = 'auto_yes' AND user_correct IS NULL "
                "ORDER BY CASE WHEN predicted_class = 'healthy' THEN 0 ELSE 1 END, created_day"
            ).fetchall()
            for row in extra:
                if remaining <= bounded_max:
                    break
                if str(row["sample_id"]) in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(str(row["sample_id"]))
                remaining -= 1
        if selected:
            placeholders = ",".join("?" * len(selected))
            connection.execute(
                f"UPDATE training_samples SET prune_status = 'eligible' "
                f"WHERE sample_id IN ({placeholders})",
                tuple(str(row["sample_id"]) for row in selected),
            )
    removed_ids: list[str] = []
    for row in selected:
        image_key = str(row["image_key"])
        if Path(image_key).name != image_key:
            continue
        try:
            (TRAINING_IMAGE_DIR / image_key).unlink(missing_ok=True)
            removed_ids.append(str(row["sample_id"]))
        except OSError:
            continue
    with _write_lock, _connect() as connection:
        if removed_ids:
            placeholders = ",".join("?" * len(removed_ids))
            connection.execute(
                f"DELETE FROM training_samples WHERE sample_id IN ({placeholders}) "
                "AND prune_status = 'eligible'",
                tuple(removed_ids),
            )
        connection.execute(
            "UPDATE training_samples SET prune_status = 'retained' "
            "WHERE prune_status = 'eligible'"
        )
    return {
        "receipts": expired_receipts,
        "samples": len(removed_ids),
        "images": len(removed_ids),
    }


def schema_sql() -> str:
    """Expose the declared schema to privacy tests and offline tooling."""
    return _SCHEMA


__all__ = [
    "EXPORT_VERSION",
    "FeedbackResult",
    "RECEIPT_TTL_SECONDS",
    "SCHEMA_VERSION",
    "SanitizedTrainingImage",
    "TRAINING_DB_PATH",
    "TRAINING_IMAGE_DIR",
    "TrainingStoreError",
    "apply_feedback",
    "blur_score_bucket",
    "complete_receipt_without_sample",
    "count_samples_for_day",
    "deterministically_sample",
    "extract_safe_leaf_crop",
    "init_db",
    "issue_feedback_receipt",
    "metadata_is_absent",
    "prune_training_data",
    "route_model_review",
    "sample_by_receipt",
    "sanitize_training_image",
    "schema_sql",
    "store_feedback_upload",
    "store_sample",
    "training_counts",
]
