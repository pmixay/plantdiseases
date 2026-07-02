"""
PlantDiseases Server — FastAPI backend with the PlantVision two-stage
houseplant disease pipeline.

Stage 1:  YOLOv8n              — locate plant leaves (single class)
Stage 2:  DINOv2 ViT-S/14      — frozen backbone + LoRA + MLP head
                                 (the ``gamma`` family), classifies
                                 cropped leaf regions into 9 classes
                                 incl. a ``not_a_plant`` head; mean-voted
                                 across bboxes, served via ONNX.

Model families (per-bundle ``classes.json -> model_family``): ``aqua``
(MobileNetV3-Small, distilled → TFLite INT8, on-device), ``gamma``
(production DINOv2 ViT-S/14, default), ``frontier`` (DINOv2 ViT-B/14,
off by default — needs a GPU host). Two safety gates wrap Stage 2: a
variance-of-Laplacian blur gate and an OOD AND-gate, with temperature
scaling + RAPS conformal sets for honest "I'm not sure" output.
"""

import asyncio
import contextlib
import copy
import functools
import hashlib
import io
import ipaddress
import json
import logging
import math
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

# Imports work two ways:
#   1) Inside Docker (`uvicorn main:app` from /app, flat copy) — `from
#      ai_services import …` resolves directly.
#   2) From the repo root (`python -m server.main` or `uvicorn
#      server.main:app`) — same names live under the `server.` package.
# The try/except below covers both without forcing PYTHONPATH gymnastics.
try:
    from . import labeling
    from .ai_services import (  # type: ignore[import-not-found]
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
        species_common_diseases,
    )
    from .community import (
        COMMUNITY_THUMB_DIR,
    )
    from .community import (
        clear_wants_opinion as community_clear_wants_opinion,
    )
    from .community import (
        daily_opinion_count_for_ip_hash as community_daily_opinion_writes,
    )
    from .community import (
        daily_write_count_for_ip_hash as community_daily_writes,
    )
    from .community import (
        delete_for_user as community_delete_for_user,
    )
    from .community import (
        density_grid as community_density_grid,
    )
    from .community import (
        get_user_data as community_get_user_data,
    )
    from .community import (
        init_db as init_community_db,
    )
    from .community import (
        map_scans as community_map_scans,
    )
    from .community import (
        nearby_scans as community_nearby_scans,
    )
    from .community import (
        opinion_counts as community_opinion_counts,
    )
    from .community import (
        opinion_counts_for_user as community_opinion_counts_for_user,
    )
    from .community import (
        opinion_open as community_opinion_open,
    )
    from .community import (
        opinions_by_author as community_opinions_by_author,
    )
    from .community import (
        opinions_for as community_opinions_for,
    )
    from .community import (
        owner_of as community_owner_of,
    )
    from .community import (
        popular_snapshot as community_popular_snapshot,
    )
    from .community import (
        prune_older_than as community_prune_older_than,
    )
    from .community import (
        record_opinion as community_record_opinion,
    )
    from .community import (
        record_scan as community_record_scan,
    )
    from .community import (
        record_view as community_record_view,
    )
    from .community import (
        region_summary as community_region_summary,
    )
    from .community import (
        resolve_scan_token as community_resolve_scan_token,
    )
    from .community import (
        scan_counts as community_scan_counts,
    )
    from .community import (
        scan_exists as community_scan_exists,
    )
    from .community import (
        scans_by_species as community_scans_by_species,
    )
    from .community import (
        set_wants_opinion as community_set_wants_opinion,
    )
    from .community import (
        stats_snapshot as community_stats_snapshot,
    )
    from .community import (
        view_counts as community_view_counts,
    )
    from .community import (
        wants_opinion as community_wants_opinion,
    )
    from .config import get, get_bool, get_float, get_int, get_list, get_str
    from .countries import COUNTRY_NAMES, to_iso_alpha2
    from .diseases_data import (
        DISEASES_DATABASE,
        lookup_pet_toxicity,
    )
    from .feedback import (
        attach_image_path as feedback_attach_image_path,
    )
    from .feedback import (
        daily_write_count_for_ip_hash as feedback_daily_writes,
    )
    from .feedback import (
        delete_for_user as feedback_delete_for_user,
    )
    from .feedback import (
        feedback_counts,
        hash_client_ip,
        is_scan_id_issued,
        record_feedback,
        record_issued_scan_id,
        save_feedback_image,
    )
    from .feedback import (
        get_user_data as feedback_get_user_data,
    )
    from .feedback import (
        init_db as init_feedback_db,
    )
    from .feedback import (
        latest_for_scan as feedback_latest_for_scan,
    )
    from .feedback import (
        prune_older_than as feedback_prune_older_than,
    )
    from .feedback import (
        scan_ids_for_user as feedback_scan_ids_for_user,
    )
    from .pipeline import PlantDiseasePipeline
    from .plant_photos import (
        get_or_fetch as plant_photo_get_or_fetch,
    )
    from .plant_photos import (
        is_allowed_url as plant_photo_allowed,
    )
    from .plant_photos import (
        media_type_for as plant_photo_media_type,
    )
    from .plant_photos import (
        name_proxy_path as plant_photo_name_proxy,
    )
    from .plant_photos import (
        proxy_path as plant_photo_proxy,
    )
    from .popular_plants import popular_for
    from .push import (
        register as push_register,
    )
    from .push import (
        send_opinion_push as push_send_opinion,
    )
    from .species_info import fetch_species_info
    from .symptoms import diagnose as symptom_diagnose
    from .telemetry import (
        daily_writes as telemetry_daily_writes,
    )
    from .telemetry import (
        init_db as init_telemetry_db,
    )
    from .telemetry import (
        prune_older_than as telemetry_prune_older_than,
    )
    from .telemetry import (
        record_error as telemetry_record_error,
    )
except ImportError:
    import labeling
    from ai_services import (
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
        species_common_diseases,
    )
    from community import (
        COMMUNITY_THUMB_DIR,
    )
    from community import (
        clear_wants_opinion as community_clear_wants_opinion,
    )
    from community import (
        daily_opinion_count_for_ip_hash as community_daily_opinion_writes,
    )
    from community import (
        daily_write_count_for_ip_hash as community_daily_writes,
    )
    from community import (
        delete_for_user as community_delete_for_user,
    )
    from community import (
        density_grid as community_density_grid,
    )
    from community import (
        get_user_data as community_get_user_data,
    )
    from community import (
        init_db as init_community_db,
    )
    from community import (
        map_scans as community_map_scans,
    )
    from community import (
        nearby_scans as community_nearby_scans,
    )
    from community import (
        opinion_counts as community_opinion_counts,
    )
    from community import (
        opinion_counts_for_user as community_opinion_counts_for_user,
    )
    from community import (
        opinion_open as community_opinion_open,
    )
    from community import (
        opinions_by_author as community_opinions_by_author,
    )
    from community import (
        opinions_for as community_opinions_for,
    )
    from community import (
        owner_of as community_owner_of,
    )
    from community import (
        popular_snapshot as community_popular_snapshot,
    )
    from community import (
        prune_older_than as community_prune_older_than,
    )
    from community import (
        record_opinion as community_record_opinion,
    )
    from community import (
        record_scan as community_record_scan,
    )
    from community import (
        record_view as community_record_view,
    )
    from community import (
        region_summary as community_region_summary,
    )
    from community import (
        resolve_scan_token as community_resolve_scan_token,
    )
    from community import (
        scan_counts as community_scan_counts,
    )
    from community import (
        scan_exists as community_scan_exists,
    )
    from community import (
        scans_by_species as community_scans_by_species,
    )
    from community import (
        set_wants_opinion as community_set_wants_opinion,
    )
    from community import (
        stats_snapshot as community_stats_snapshot,
    )
    from community import (
        view_counts as community_view_counts,
    )
    from community import (
        wants_opinion as community_wants_opinion,
    )
    from config import get, get_bool, get_float, get_int, get_list, get_str
    from countries import COUNTRY_NAMES, to_iso_alpha2
    from diseases_data import (
        DISEASES_DATABASE,
        lookup_pet_toxicity,
    )
    from feedback import (
        attach_image_path as feedback_attach_image_path,
    )
    from feedback import (
        daily_write_count_for_ip_hash as feedback_daily_writes,
    )
    from feedback import (
        delete_for_user as feedback_delete_for_user,
    )
    from feedback import (
        feedback_counts,
        hash_client_ip,
        is_scan_id_issued,
        record_feedback,
        record_issued_scan_id,
        save_feedback_image,
    )
    from feedback import (
        get_user_data as feedback_get_user_data,
    )
    from feedback import (
        init_db as init_feedback_db,
    )
    from feedback import (
        latest_for_scan as feedback_latest_for_scan,
    )
    from feedback import (
        prune_older_than as feedback_prune_older_than,
    )
    from feedback import (
        scan_ids_for_user as feedback_scan_ids_for_user,
    )
    from pipeline import PlantDiseasePipeline
    from plant_photos import (
        get_or_fetch as plant_photo_get_or_fetch,
    )
    from plant_photos import (
        is_allowed_url as plant_photo_allowed,
    )
    from plant_photos import (
        media_type_for as plant_photo_media_type,
    )
    from plant_photos import (
        name_proxy_path as plant_photo_name_proxy,
    )
    from plant_photos import (
        proxy_path as plant_photo_proxy,
    )
    from popular_plants import popular_for
    from push import (
        register as push_register,
    )
    from push import (
        send_opinion_push as push_send_opinion,
    )
    from species_info import fetch_species_info
    from symptoms import diagnose as symptom_diagnose
    from telemetry import (
        daily_writes as telemetry_daily_writes,
    )
    from telemetry import (
        init_db as init_telemetry_db,
    )
    from telemetry import (
        prune_older_than as telemetry_prune_older_than,
    )
    from telemetry import (
        record_error as telemetry_record_error,
    )


_SERVER_VERSION = "1.0"


def _resolve_app_version() -> str:
    """Resolve the SERVER / release version.

    This is decoupled from the MODEL version (``models/classes.json ->
    model_version``, e.g. 2.6.8), which is surfaced separately by
    ``/api/version``. Prefer the package ``__version__``; when the server runs
    as a flat module (``main:app`` in the Docker image) that import fails, so
    fall back to the same hardcoded server version (kept in sync with
    ``__init__.py``) — never the model version, which would make the server
    string track the model.
    """
    try:
        from . import __version__ as pkg_version  # type: ignore

        return str(pkg_version)
    except Exception:
        return _SERVER_VERSION


APP_VERSION = _resolve_app_version()

# Limit Pillow decompression to prevent zip-bomb DoS
Image.MAX_IMAGE_PIXELS = 20_000_000  # ~4500×4500

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = request_id_var.get()
        if trace_id:
            log_entry["trace_id"] = trace_id
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path(get_str("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

USE_STRUCTURED_LOGS = get("STRUCTURED_LOGS", False, cast=bool)
if USE_STRUCTURED_LOGS:
    _log_fmt = JSONFormatter(datefmt="%Y-%m-%d %H:%M:%S")
else:
    _log_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(_log_fmt)

file_handler = RotatingFileHandler(
    LOG_DIR / "server.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(_log_fmt)

logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])
logger = logging.getLogger("plantdiseases")

# ── Configuration ────────────────────────────────────────────────────
MODELS_DIR = Path(get_str("MODELS_DIR", "models"))
DETECTOR_PATH = MODELS_DIR / "detector.pt"       # YOLOv8 weights (auto-prefers detector.onnx if present)
# PlantVision classifier. ONNX-only and architecture-neutral — it serves
# whatever backbone+head the shipped artefact declares (classes.json
# carries the canonical architecture + img_size). Preference order:
# classifier_int8.onnx → classifier.onnx. This path is the anchor used to
# locate those files + classes.json next to it (the .pth itself is not
# loaded at serve time).
CLASSIFIER_PATH = MODELS_DIR / "classifier.onnx"

# ── Model families ───────────────────────────────────────────────────
# Three capacity tiers share one runtime + one class set (classes.json ->
# model_family). The client picks one per scan:
#   • aqua     — light on-device TFLite student; runs entirely in the app,
#                so the server normally never sees an ``aqua`` request. If
#                one does arrive (e.g. an online client falling back) we
#                serve it with the gamma pipeline for parity.
#   • gamma    — the current medium model; the default and always loaded.
#   • frontier — a high-capacity (ViT-B/14, ~86M param) model. Its server
#                inference is GATED OFF by default: we have no GPU host, so
#                operators must explicitly enable it AND provision its
#                artefacts. When disabled, /api/analyze?family=frontier
#                returns a graceful ``available:false`` payload (never 500)
#                and /api/capabilities reports it unavailable so the app can
#                grey it out in the model picker.
KNOWN_FAMILIES = ("aqua", "gamma", "frontier")
DEFAULT_FAMILY = "gamma"
FRONTIER_ENABLED = get_bool("FRONTIER_ENABLED", False)
# Frontier classifier lives in its own dir so its classes.json /
# temperature.json / conformal.json never collide with gamma's. The
# detector is shared (the gamma YOLOv8n crops are family-agnostic).
FRONTIER_MODELS_DIR = Path(get_str("FRONTIER_MODELS_DIR", str(MODELS_DIR / "frontier")))
FRONTIER_CLASSIFIER_PATH = FRONTIER_MODELS_DIR / "classifier.onnx"

# ── Scan-collection trust knobs ──────────────────────────────────────
# route_scan auto-accepts confident scans (singleton conformal set, or high
# calibrated confidence when conformal is off) as ``auto_yes``; the uncertain
# tail is tagged ``pending`` (model on the edge — high training value, always
# kept). There is no human review queue: the store is exported and judged
# offline (docs/DATASET_LABELING_PLAN.md, tools/export_data.py).
LABELING_TRUST_CONFORMAL = get_bool("LABELING_TRUST_CONFORMAL", True)
LABELING_CONF_HIGH = get_float("LABELING_CONF_HIGH", 0.85)
# Value-driven scan retention: every disease class is high training signal and
# is always kept; the abundant easy class (healthy; not_a_plant is dropped
# upstream) is sampled at SCAN_COMMON_KEEP_RATE so confident-healthy photos
# can't drown the disease corpus.
DISEASE_CLASSES = {
    "gray_mold_botrytis", "leaf_spot", "leaf_scorch_underwatering",
    "nutrient_deficiency", "pests", "powdery_mildew", "viral_disease",
}

# ── Production scan recording ────────────────────────────────────────
# Every non-not_a_plant (and non-blur — the blur gate emits a synthetic NAP)
# analyze result is recorded ANONYMIZED for model improvement: re-encoded
# (EXIF/GPS dropped), downscaled, no user identifiers — the only linkage is
# the response scan_id, and the user↔scan_id join lives solely in feedback.db
# (which the GDPR delete cascade sweeps). Routed by route_scan(): confident →
# auto_yes, suspicious → pending (kept for offline review). Disclosed via the
# blocking first-launch consent screen (Android) + website/privacy.html.
SCAN_RECORDING_ENABLED = get_bool("SCAN_RECORDING_ENABLED", True)
SCAN_RETENTION_DAYS = get_int("SCAN_RETENTION_DAYS", 90)
SCAN_MAX_IMAGES = get_int("SCAN_MAX_IMAGES", 20000)
# Value caps: confident scans of the abundant easy class (healthy) are sampled
# at this rate so a flood of confident-healthy photos can't drown the disease
# corpus; per-class daily cap bounds disk regardless of class.
SCAN_COMMON_KEEP_RATE = get_float("SCAN_COMMON_KEEP_RATE", 0.1)
SCAN_DAILY_CAP_PER_CLASS = get_int("SCAN_DAILY_CAP_PER_CLASS", 50)
SCAN_IMG_MAX_SIDE = 1024  # anonymization downscale bound (not configurable)

# ── Guide example photos ─────────────────────────────────────────────
# Disease/pest example photos used by the app's guide + after-scan strip.
# They live with the SERVER (server/static/guide/<key>.png) rather than the
# APK so the app stays small; the client downloads + disk-caches them, so
# once seen they remain available offline. Path is resolved relative to this
# module so it works whether uvicorn runs from server/ or the repo root.
GUIDE_STATIC_DIR = Path(get_str(
    "GUIDE_STATIC_DIR", str(Path(__file__).resolve().parent / "static" / "guide")))

# ── Aqua on-device model hosting ─────────────────────────────────────
# The Aqua TFLite student is served to the app so it can download + cache it
# (and pick up updates) instead of bundling it in the APK — same rationale as
# the guide photos. The artefact is provisioned OUT OF BAND (a trained
# .tflite, never committed, like the server weights); when absent the manifest
# reports available:false and the app simply falls back to the server model.
# Drop ``aqua_classifier.tflite`` + ``classes.json`` (from notebook §5) here.
AQUA_STATIC_DIR = Path(get_str(
    "AQUA_STATIC_DIR", str(Path(__file__).resolve().parent / "static" / "aqua")))

# Model weights + calibration artefacts are NOT committed to the repo and
# are NOT auto-downloaded. Provision them out of band: drop the trained
# files (detector.{pt,onnx}, classifier.onnx, classes.json, ...) into
# MODELS_DIR before starting the server. Boot is fail-fast — the pipeline
# refuses to start if a required artefact is missing.
MAX_FILE_SIZE = get_int("MAX_FILE_SIZE", 10 * 1024 * 1024)  # 10 MB
MAX_IMAGE_DIMENSION = get_int("MAX_IMAGE_DIMENSION", 4096)
# Reject degenerate thumbnails. A tiny upload (e.g. 8x8) carries no
# diagnostic signal but the blur gate passes it (upscaled noise has
# high Laplacian variance) and the detector/classifier then emit a
# confident — and meaningless — verdict (observed live: an 8x8 noise
# image scored viral_disease @0.72 / severity=high). Anything below
# this on the short side is short-circuited to a clean 400 so the
# client asks the user to retake instead of trusting a fake diagnosis.
MIN_IMAGE_DIMENSION = get_int("MIN_IMAGE_DIMENSION", 48)
INFERENCE_TIMEOUT = get_float("INFERENCE_TIMEOUT", 30.0)

# Same-photo dedup. Users frequently re-upload the same JPEG within
# minutes (gallery picker mis-tap, "did the verdict change?" curiosity,
# the test harness). Caching the *response* by SHA-256 of the upload
# bytes saves a full pipeline cycle + a PlantNet call AND lets the
# client render a friendly "you saw this one earlier" banner. The
# cache is intentionally in-memory only: it loses state on restart,
# which is the right behaviour while the model itself is in flux.
#
# Matching is exact-bytes only (the SHA-256 key): a re-encoded or
# resized re-upload is treated as a fresh photo on purpose, so a
# perceptually-similar but genuinely different image can never be served
# someone else's diagnosis. The TTL is 12h — long enough that the same
# scan re-opened later in the day still hits, short enough that a reship
# of the model isn't masked by stale verdicts for more than half a day.
ANALYZE_CACHE_TTL_SECONDS = get_int("ANALYZE_CACHE_TTL_SECONDS", 24 * 3600)
ANALYZE_CACHE_MAX_ENTRIES = get_int("ANALYZE_CACHE_MAX_ENTRIES", 512)
_analyze_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_analyze_cache_lock = threading.Lock()

def _analyze_cache_get(cache_key: str) -> dict | None:
    """Return a previously-stored analyse response keyed by
    ``<image_sha>:<plantnet_lang>``, or ``None`` if there's nothing
    fresh under that key. Updates LRU recency on hit so frequent
    re-uploads stay warm."""
    now = time.time()
    with _analyze_cache_lock:
        item = _analyze_cache.get(cache_key)
        if item is None:
            return None
        cached_at, payload = item
        if now - cached_at > ANALYZE_CACHE_TTL_SECONDS:
            _analyze_cache.pop(cache_key, None)
            return None
        _analyze_cache.move_to_end(cache_key)
        return {"cached_at": cached_at, "payload": payload}

def _analyze_cache_put(cache_key: str, payload: dict) -> None:
    now = time.time()
    with _analyze_cache_lock:
        _analyze_cache[cache_key] = (now, payload)
        _analyze_cache.move_to_end(cache_key)
        while len(_analyze_cache) > ANALYZE_CACHE_MAX_ENTRIES:
            _analyze_cache.popitem(last=False)
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
# Bare RIFF is intentionally NOT in this tuple: RIFF can be WAV / AVI /
# ANI as well as WebP, and any of those would have passed a generic
# `head.startswith(b"RIFF")` magic-byte check. WebP has its own
# explicit `RIFF<size>WEBP` validator below.
IMAGE_MAGIC = (
    b"\xff\xd8\xff",            # JPEG
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"BM",                      # BMP
)


def _is_webp(head: bytes, contents: bytes) -> bool:
    """Strict WebP detection: ``RIFF`` at offset 0 AND ``WEBP`` at offset 8."""
    return (
        head.startswith(b"RIFF")
        and len(contents) >= 12
        and contents[8:12] == b"WEBP"
    )


_FILENAME_LOG_SAFE = str.maketrans({c: " " for c in "\r\n\t\v\f\x00"})


def _safe_filename(name: str | None, *, max_len: int = 80) -> str:
    """Strip control chars + truncate user-controlled filenames before
    logging — defends against log injection ("upload\\nFAKE LOG LINE").
    """
    if not name:
        return "<no-filename>"
    cleaned = str(name).translate(_FILENAME_LOG_SAFE)[:max_len]
    return cleaned or "<empty-filename>"


RATE_LIMIT_RPS = get_float("RATE_LIMIT_RPS", 2.0)
# Burst capacity on top of the steady-state rate. One user SCAN fans out into
# many requests — analyze + identify + capabilities + aqua/manifest + care-plan
# + species-info + community/nearby + community/by-species + opinions — so a
# burst of 5 was far too small: a single scan overflowed the bucket, each
# overflow was a 429, and 5 of those tripped the abuse auto-ban below (the
# "Server error: 403 for no reason" users saw). 20 absorbs a couple of scans'
# fan-out back-to-back before the steady rate takes over.
RATE_LIMIT_BURST = get_float("RATE_LIMIT_BURST", 20.0)
TRUSTED_PROXIES = set(get_list("TRUSTED_PROXIES", []))

# API Keys configuration
REQUIRE_API_KEY = get("REQUIRE_API_KEY", False, cast=bool)
api_keys_raw = get("API_KEYS", {})
if isinstance(api_keys_raw, str):
    try:
        API_KEYS = json.loads(api_keys_raw)
    except Exception:
        API_KEYS = {}
else:
    API_KEYS = api_keys_raw or {}

# Thread-safe structures for Quotas & Abuse/Banning
_quota_lock = threading.Lock()
_in_memory_quotas: dict[str, dict[str, int]] = {}  # key -> {date_str: count}

_abuse_lock = threading.Lock()
_in_memory_violations: dict[str, tuple[float, int]] = {} # identifier -> (reset_time, count)
_in_memory_bans: dict[str, float] = {} # identifier -> banned_until

# Abuse auto-ban (temporary). Targets SUSTAINED floods, not a normal client
# that occasionally clips the rate limiter — the old threshold of 5 banned
# real users whose per-scan request fan-out briefly exceeded the burst, turning
# a transient 429 into a 5-minute 403. You now need ~40 over-limit requests in
# a minute (well beyond any legitimate UI) to earn a ban. Configurable so
# operators can tighten/loosen without a code change.
VIOLATION_THRESHOLD = get_int("VIOLATION_THRESHOLD", 40)
VIOLATION_WINDOW = get_float("VIOLATION_WINDOW", 60.0)
BAN_DURATION = get_float("BAN_DURATION", 300.0)

def _check_and_increment_quota(api_key: str, daily_limit: int) -> bool:
    """Check if the daily quota is exceeded, and if not, increment it.
    Returns True if quota is NOT exceeded, False if exceeded.
    """
    if daily_limit <= 0:
        return True

    current_day = time.strftime("%Y-%m-%d", time.gmtime())

    with _quota_lock:
        if api_key not in _in_memory_quotas:
            _in_memory_quotas[api_key] = {}
        day_dict = _in_memory_quotas[api_key]
        for old_day in list(day_dict.keys()):
            if old_day != current_day:
                day_dict.pop(old_day, None)
        current_count = day_dict.get(current_day, 0)
        if current_count >= daily_limit:
            return False
        day_dict[current_day] = current_count + 1
        return True

def _is_banned(identifier: str) -> bool:
    now = time.time()
    with _abuse_lock:
        banned_until = _in_memory_bans.get(identifier, 0.0)
        if banned_until > now:
            return True
        elif identifier in _in_memory_bans:
            del _in_memory_bans[identifier]
        return False

def _record_violation(identifier: str) -> None:
    now = time.time()
    with _abuse_lock:
        # Bound the violation map: entries whose window already elapsed are
        # dead weight, and under IP churn (public deploy behind CGNAT /
        # scanners) the dict would otherwise grow without limit.
        if len(_in_memory_violations) > 10_000:
            for key in [
                k for k, (reset, _c) in _in_memory_violations.items() if now > reset
            ]:
                del _in_memory_violations[key]
        reset_time, count = _in_memory_violations.get(identifier, (now + VIOLATION_WINDOW, 0))
        if now > reset_time:
            reset_time = now + VIOLATION_WINDOW
            count = 0
        count += 1
        _in_memory_violations[identifier] = (reset_time, count)
        if count >= VIOLATION_THRESHOLD:
            _in_memory_bans[identifier] = now + BAN_DURATION
            logger.warning("Temporary ban applied to identifier=%s until %s", identifier, now + BAN_DURATION)

def _extract_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None

# Daily per-IP write quotas. Stops one bad actor from filling the
# feedback / community SQLite files with spam (which would also
# poison the metrics surface). Reads from the same hashed-IP column
# the rate-limit middleware can't see, so it survives across server
# restarts unlike the in-memory rate bucket.
FEEDBACK_DAILY_QUOTA = get_int("FEEDBACK_DAILY_QUOTA", 200)
COMMUNITY_DAILY_QUOTA = get_int("COMMUNITY_DAILY_QUOTA", 50)
COMMUNITY_OPINION_DAILY_QUOTA = get_int("COMMUNITY_OPINION_DAILY_QUOTA", 60)
# Client error/problem telemetry ingestion. Errors-only, opt-out here; the
# cap + retention live in telemetry.py.
TELEMETRY_ENABLED = get_bool("TELEMETRY_ENABLED", True)
TELEMETRY_DAILY_CAP_PER_IP = get_int("TELEMETRY_DAILY_CAP_PER_IP", 200)
TELEMETRY_RETENTION_DAYS = get_int("TELEMETRY_RETENTION_DAYS", 30)
# Retention windows (days). Daily background task in the FastAPI
# lifespan drops rows past this age.
FEEDBACK_RETENTION_DAYS = get_int("FEEDBACK_RETENTION_DAYS", 180)
COMMUNITY_RETENTION_DAYS = get_int("COMMUNITY_RETENTION_DAYS", 90)
PRUNE_INTERVAL_SECONDS = get_int("PRUNE_INTERVAL_SECONDS", 24 * 60 * 60)

# Server-issued scan_ids stay valid for this long. Long enough that
# a user can scan today and leave feedback in a week; short enough
# that a leaked id can't be reused indefinitely.
ISSUED_SCAN_ID_TTL_DAYS = get_int("ISSUED_SCAN_ID_TTL_DAYS", 30)

# One inference at a time by default. Each ONNX session now uses all
# physical cores (ONNX_INTRA_OP_THREADS=0, see classifier.py/detector.py),
# so running two concurrent inferences would oversubscribe the CPU and make
# *both* slower. With the heavy DINOv2 ViT backbone, finishing one scan fast
# beats interleaving two — single-user latency is what matters for this
# rate-limited (1 RPS) workload. Operators expecting real concurrency on a
# many-core host can raise this and lower ONNX_INTRA_OP_THREADS to match.
#
# ONLY ``pipeline.analyze`` runs on this executor. Everything else that
# needs a thread (SQLite reads/writes, PlantNet / OpenRouter / Wikipedia
# HTTP, FCM pushes) runs on the event loop's default thread pool —
# putting it here would queue millisecond DB ops and external HTTP
# behind a 30-second inference (head-of-line blocking), stalling every
# non-scan endpoint whenever a scan is in flight.
INFER_WORKERS = get_int("INFER_WORKERS", 1)
_inference_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_inference_executor() -> ThreadPoolExecutor:
    """Lazily (re)create the inference executor so test harnesses that cycle
    the FastAPI lifespan can reuse it after a previous shutdown.

    The lock prevents two concurrent cold-start requests from both
    constructing executors and leaking one. In normal operation the
    lifespan handler creates the executor eagerly so this path only
    runs in tests.
    """
    global _inference_executor
    with _executor_lock:
        if _inference_executor is None or getattr(_inference_executor, "_shutdown", False):
            _inference_executor = ThreadPoolExecutor(max_workers=INFER_WORKERS)
        return _inference_executor

# ── Thread-safe server metrics ──────────────────────────────────────
SERVER_START_TIME: float = 0.0
_metrics_lock = threading.Lock()
_total_requests = 0
_total_analyses = 0
_total_errors = 0
_latency_sum_ms = 0.0
_latency_count = 0
_class_counts: dict[str, int] = {}


def _record_request() -> None:
    global _total_requests
    with _metrics_lock:
        _total_requests += 1


def _record_analysis(class_name: str, elapsed_ms: float) -> None:
    global _total_analyses, _latency_sum_ms, _latency_count
    with _metrics_lock:
        _total_analyses += 1
        _latency_sum_ms += elapsed_ms
        _latency_count += 1
        _class_counts[class_name] = _class_counts.get(class_name, 0) + 1


def _record_error() -> None:
    global _total_errors
    with _metrics_lock:
        _total_errors += 1


def _snapshot_metrics() -> dict:
    with _metrics_lock:
        avg = _latency_sum_ms / _latency_count if _latency_count else 0.0
        return {
            "total_requests": _total_requests,
            "total_analyses": _total_analyses,
            "total_errors": _total_errors,
            "avg_latency_ms": round(avg, 1),
            "class_counts": dict(_class_counts),
        }


pipeline: PlantDiseasePipeline | None = None
# Optional second pipeline for the frontier family. Stays ``None`` unless
# FRONTIER_ENABLED is set AND the frontier artefacts load cleanly at boot.
pipeline_frontier: PlantDiseasePipeline | None = None


def _normalise_family(family: str | None) -> str:
    """Map a requested family string to a known tier, defaulting to gamma.

    Unknown values fall back to the default rather than erroring — a
    forward-compatible client sending a future family name still gets a
    usable diagnosis instead of a 400.
    """
    fam = (family or "").strip().lower()
    return fam if fam in KNOWN_FAMILIES else DEFAULT_FAMILY


def _family_available(family: str) -> bool:
    """Whether the server can answer a request for this family.

    ``gamma`` is available whenever the base pipeline loaded. ``aqua`` is an
    on-device family but the server can stand in with gamma, so it tracks
    gamma's availability. ``frontier`` requires its own pipeline to be loaded.
    """
    if family == "frontier":
        return pipeline_frontier is not None
    return pipeline is not None


def _pipeline_for_family(family: str) -> PlantDiseasePipeline | None:
    """Resolve the pipeline instance that should serve this family."""
    if family == "frontier":
        return pipeline_frontier
    # gamma + aqua (server stand-in) both run on the base pipeline.
    return pipeline


# ── Rate limiter cleanup interval (seconds) ─────────────────────────
_RATE_LIMIT_CLEANUP_INTERVAL = 300


def _persist_issued_scan_id(scan_id: str) -> None:
    """Persist an issued scan_id in the issued log. Best-effort: a failed
    write must never break analyse — and, since this runs fire-and-forget on
    the executor, swallowing here also avoids an unretrieved-future warning.
    """
    try:
        record_issued_scan_id(scan_id)
    except Exception as exc:  # noqa: BLE001 — never fail analyse on the log write
        logger.warning("Failed to persist issued scan_id: %s", exc)


def _client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For only for trusted proxies."""
    peer = request.client.host if request.client else "unknown"
    if not TRUSTED_PROXIES:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    # Only trust the header if the direct peer is a configured proxy.
    if peer not in TRUSTED_PROXIES and not any(
        peer_ip in ipaddress.ip_network(net, strict=False) for net in TRUSTED_PROXIES
    ):
        return peer
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Take the leftmost non-empty entry that parses as an IP
        for entry in (e.strip() for e in xff.split(",")):
            if entry:
                try:
                    ipaddress.ip_address(entry)
                    return entry
                except ValueError:
                    continue
    return peer


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token-bucket rate limiter with burst capacity.

    Each client IP gets a private bucket of capacity ``burst``, which
    refills at ``rate`` tokens per second. A request takes one
    token; an empty bucket returns 429 with a ``Retry-After`` header
    that reflects the wait until *one* token has refilled (not the
    full bucket — clients don't need to wait for the burst window
    again, just for the next allowed call).

    The previous implementation enforced a strict minimum interval
    of ``1/rps`` between requests, which meant two requests inside
    500 ms (typical analyse + care-plan fan-out) bounced one of
    them with 429 even on low rates. The bucket model absorbs that
    burst without raising the long-term rate.
    """

    def __init__(
        self,
        app,
        requests_per_second: float = 1.0,
        burst: float = 5.0,
    ):
        super().__init__(app)
        self.rate = max(requests_per_second, 0.0)
        self.capacity = max(burst, 1.0)
        # tokens, last_refill_monotonic
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._warned_about_xff = False

    # ── backwards-compat shim used by tests that tune the limiter ────
    @property
    def min_interval(self) -> float:
        return 1.0 / self.rate if self.rate > 0 else 0.0

    @min_interval.setter
    def min_interval(self, value: float) -> None:
        # Tests poke ``mw.min_interval = 0.0`` to disable the limiter
        # for an entire fixture; preserve that contract by rebuilding
        # the rate from the value they hand us.
        self.rate = (1.0 / value) if value > 0 else float("inf")

    @property
    def _last_request(self) -> dict[str, tuple[float, float]]:
        """Legacy alias used by the rate-limited test fixture to
        ``.clear()`` the bucket between cases."""
        return self._buckets

    def _cleanup_stale(self, now: float) -> None:
        # A bucket whose tokens never dropped below capacity in the
        # last 60 s is effectively inactive — drop it. Holding every
        # client-IP indefinitely would be a memory leak under a
        # short-lived public deploy.
        if self.rate <= 0:
            return
        cutoff = now - max(self.capacity / self.rate, 60.0)
        stale = [ip for ip, (_t, last) in self._buckets.items() if last < cutoff]
        for ip in stale:
            del self._buckets[ip]

    async def dispatch(self, request: Request, call_next):
        # Health, metadata and metrics endpoints are always free
        path = request.url.path
        if path in (
            "/api/health",
            "/api/capabilities",
            "/api/metrics",
            "/api/metrics/prometheus",
        ):
            return await call_next(request)
        # Static image assets + the tiny aqua manifest are also free.
        # Opening the guide (or a thumbnail-heavy community strip) fans
        # out into a dozen image GETs at once; metering those against
        # the same token bucket as the API drained the burst budget and
        # 429'd the *next real* request — the "docs stop working while
        # images load" failure. These are cheap disk reads (mostly 304s
        # after first fetch), so exempting them is safe.
        if request.method in ("GET", "HEAD") and (
            path.startswith("/api/guide/")
            or path.startswith("/api/community/thumb/")
            or path == "/api/aqua/manifest"
        ):
            return await call_next(request)

        # Soft warning the operator missed configuring TRUSTED_PROXIES
        # while deploying behind a reverse proxy.
        if (
            not self._warned_about_xff
            and not TRUSTED_PROXIES
            and request.headers.get("x-forwarded-for")
        ):
            self._warned_about_xff = True
            logger.warning(
                "Received X-Forwarded-For but TRUSTED_PROXIES is empty — "
                "every client behind the proxy shares one rate-limit bucket. "
                "Set TRUSTED_PROXIES to the proxy CIDR to honour XFF."
            )

        client_ip = _client_ip(request)
        api_key = _extract_api_key(request)

        # 1. API Key validation and requirement check
        if REQUIRE_API_KEY:
            if not api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required. Provide via X-API-Key or Authorization header."},
                )
            if api_key not in API_KEYS:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API key."},
                )
        elif api_key and api_key not in API_KEYS:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key."},
            )

        # 2. Determine identifier and limits
        if api_key:
            identifier = f"key:{api_key}"
            # A key's config is usually a dict of overrides, but tolerate a
            # bare string/null value (e.g. {"key": "team name"}) — that key
            # then just inherits the global limits instead of 500-ing every
            # request with an AttributeError.
            key_cfg = API_KEYS.get(api_key)
            if not isinstance(key_cfg, dict):
                key_cfg = {}
            try:
                rate = float(key_cfg.get("rate_limit_rps", self.rate))
                capacity = float(key_cfg.get("burst", self.capacity))
                quota_daily = int(key_cfg.get("quota_daily", 0))
            except (TypeError, ValueError):
                rate, capacity, quota_daily = self.rate, self.capacity, 0
        else:
            identifier = f"ip:{client_ip}"
            rate = self.rate
            capacity = self.capacity
            quota_daily = 0

        # 3. Check Banned state (abuse control)
        if _is_banned(identifier):
            return JSONResponse(
                status_code=403,
                content={"detail": "API key or IP temporarily banned due to suspected abuse."},
            )

        # Bypass rate limiter checks if rate is infinity or non-positive
        if rate == float("inf") or rate <= 0:
            # 4. Daily quota still applies to unthrottled keys.
            if quota_daily > 0 and not _check_and_increment_quota(api_key, quota_daily):
                logger.warning("Daily quota exceeded for key=%s quota=%d", api_key[:12], quota_daily)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Daily quota exceeded for this API key. Try again tomorrow."},
                )
            return await call_next(request)

        # 5. Token bucket rate limit check. Runs BEFORE the daily quota
        # so a burst of rate-limited 429s doesn't burn quota for
        # requests the server never processed.
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup > _RATE_LIMIT_CLEANUP_INTERVAL:
                self._cleanup_stale(now)
                self._last_cleanup = now

            tokens, last = self._buckets.get(identifier, (capacity, now))
            elapsed = max(0.0, now - last)
            tokens = min(capacity, tokens + elapsed * rate)
            if tokens < 1.0:
                logger.warning("Rate limited identifier=%s tokens=%.2f", identifier[:24], tokens)
                deficit = 1.0 - tokens
                retry_after = max(1, int(math.ceil(deficit / rate))) if rate > 0 else 60
                self._buckets[identifier] = (tokens, now)
                
                # Record violation for abuse control
                _record_violation(identifier)
                
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={"detail": "Too many requests. Please wait before trying again."},
                )
            tokens -= 1.0
            self._buckets[identifier] = (tokens, now)

        # 6. Daily quota — only requests that passed the rate limiter
        # count against the key's daily budget.
        if quota_daily > 0 and not _check_and_increment_quota(api_key, quota_daily):
            logger.warning("Daily quota exceeded for key=%s quota=%d", api_key[:12], quota_daily)
            return JSONResponse(
                status_code=429,
                content={"detail": "Daily quota exceeded for this API key. Try again tomorrow."},
            )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Minimal set of security response headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(trace_id)
        
        _record_request()
        start = time.time()
        try:
            response: StarletteResponse = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            elapsed = time.time() - start
            logger.info(
                "REQUEST %s %s -> %d (%.3fs) IP=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
                _client_ip(request),
            )
            return response
        finally:
            request_id_var.reset(token)


# ── Lifecycle ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail-fast startup: raise (i.e. abort boot) when models can't load.

    The product contract is server-only inference; a half-broken
    pipeline that silently degrades to heuristics is worse than no
    server because clients can't tell the difference until users
    notice bad diagnoses.
    """
    global pipeline, pipeline_frontier, SERVER_START_TIME, _inference_executor
    SERVER_START_TIME = time.time()
    _inference_executor = _get_inference_executor()
    init_feedback_db()
    init_community_db()
    init_telemetry_db()
    labeling.init_labeling_db()
    # Model artefacts must already be present under MODELS_DIR (provisioned
    # out of band). The pipeline constructor is fail-fast and aborts boot
    # if a required weight / classes.json is missing.
    pipeline = PlantDiseasePipeline(
        detector_path=DETECTOR_PATH,
        classifier_path=CLASSIFIER_PATH,
    )
    logger.info(
        "Pipeline initialised (classifier=%s, %d classes, family=%s)",
        pipeline.classifier.backend,
        pipeline.classifier.NUM_CLASSES,
        pipeline.classifier.model_family,
    )
    # Frontier is opt-in. Unlike gamma, a missing/broken frontier bundle is
    # NOT fail-fast: we log and leave ``pipeline_frontier=None`` so the
    # always-on gamma path still serves. The picker / analyze gate then
    # report frontier unavailable. This keeps "enabled in config but
    # artefacts not provisioned yet" from taking the whole server down.
    if FRONTIER_ENABLED:
        try:
            pipeline_frontier = PlantDiseasePipeline(
                detector_path=DETECTOR_PATH,
                classifier_path=FRONTIER_CLASSIFIER_PATH,
            )
            logger.info(
                "Frontier pipeline initialised (classifier=%s, %d classes, family=%s)",
                pipeline_frontier.classifier.backend,
                pipeline_frontier.classifier.NUM_CLASSES,
                pipeline_frontier.classifier.model_family,
            )
        except Exception as exc:  # noqa: BLE001 — frontier must degrade, not abort boot
            pipeline_frontier = None
            logger.error(
                "FRONTIER_ENABLED but frontier pipeline failed to load from %s: %s "
                "— frontier will report unavailable.",
                FRONTIER_CLASSIFIER_PATH, exc,
            )
    else:
        logger.info("Frontier family disabled (FRONTIER_ENABLED=false)")
    # Daily prune task. Runs in the event loop because the work is
    # I/O-bound (a single DELETE per table); no thread pool needed.
    # Cancelled cleanly on shutdown so the lifespan exit doesn't
    # leak the task.
    prune_task = asyncio.create_task(_prune_loop())
    # Warm the curated placeholder plants' lead images so a fresh country
    # (no community scans yet) paints its popular carousel without a wait.
    warm_task = asyncio.create_task(_warm_placeholder_images())
    try:
        yield
    finally:
        logger.info("Server shutting down")
        prune_task.cancel()
        warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prune_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await warm_task
        with _executor_lock:
            if _inference_executor is not None:
                # `wait=True` lets in-flight inferences drain. The bound is
                # INFERENCE_TIMEOUT (default 30 s) — anything beyond that is
                # already a stuck request. cancel_futures=True drops queued
                # work that hasn't started yet.
                _inference_executor.shutdown(wait=True, cancel_futures=True)
                _inference_executor = None


async def _prune_loop() -> None:
    """Background scheduler: prune feedback + community rows past
    retention every ``PRUNE_INTERVAL_SECONDS``. First pass runs
    immediately so a restart catches up on stale rows.
    """
    while True:
        try:
            removed_fb = await asyncio.get_running_loop().run_in_executor(
                None, feedback_prune_older_than, FEEDBACK_RETENTION_DAYS,
            )
            removed_cm = await asyncio.get_running_loop().run_in_executor(
                None, community_prune_older_than, COMMUNITY_RETENTION_DAYS,
            )
            removed_tm = await asyncio.get_running_loop().run_in_executor(
                None, telemetry_prune_older_than, TELEMETRY_RETENTION_DAYS,
            )
            if removed_fb or removed_cm or removed_tm:
                logger.info(
                    "Prune pass: feedback=%d community=%d telemetry=%d",
                    removed_fb, removed_cm, removed_tm,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Prune pass failed (will retry tomorrow): %s", exc)
        # Bound the recorded-scan footprint (retention + class-balanced cap).
        if SCAN_RECORDING_ENABLED:
            try:
                pruned = await asyncio.get_running_loop().run_in_executor(
                    None, labeling.prune_user_scans,
                    SCAN_RETENTION_DAYS, SCAN_MAX_IMAGES, DISEASE_CLASSES,
                )
                if pruned:
                    logger.info("Pruned %d recorded user scans", pruned)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("User-scan prune failed: %s", exc)
        try:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


app = FastAPI(
    title="PlantDiseases API",
    description="AI-powered plant disease detection — two-stage pipeline",
    version=APP_VERSION,
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = get_list(
    "CORS_ORIGINS", ["http://localhost:3000", "http://127.0.0.1:3000"],
)
if ALLOWED_ORIGINS == ["*"]:
    logger.warning("CORS_ORIGINS=* is insecure for production; restrict it in deployment.")

# Middleware order: Starlette wraps in reverse-registration order, so the
# LAST middleware added is the OUTERMOST (runs first on the request, last
# on the response).
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    requests_per_second=RATE_LIMIT_RPS,
    burst=RATE_LIMIT_BURST,
)
# Treatment / prevention lists round-trip 5–10 KB of bilingual JSON.
# Halving that on the wire matters more for slow mobile uplinks than
# the ~10 µs CPU cost on the server side.
app.add_middleware(GZipMiddleware, minimum_size=1024)
# CORS is added LAST so it is the outermost layer: preflight (OPTIONS)
# requests are answered before they reach the rate limiter, and the
# Access-Control-* headers are attached even to error responses produced
# by the inner middleware (rate-limit 429s, etc.) so the browser can read
# them instead of reporting an opaque CORS failure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    # Tightened — the Android client only sends Content-Type. Avoid
    # the wildcard so a XS-Search via a custom header isn't trivially
    # mountable from a malicious origin we forgot to exclude.
    allow_headers=["Content-Type", "Accept"],
)


def _validate_image_bytes(contents: bytes) -> None:
    """Validate file size and magic bytes. Raises HTTPException on failure."""
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file received")
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB",
        )
    head = contents[:16]
    if any(head.startswith(sig) for sig in IMAGE_MAGIC):
        return
    if _is_webp(head, contents):
        return
    raise HTTPException(
        status_code=400,
        detail="Unrecognised image format. Allowed: JPEG, PNG, WebP, BMP",
    )


async def _read_upload(image: UploadFile) -> bytes:
    """Read the upload body with a hard cap.

    1. If the client sent ``Content-Length`` and it exceeds ``MAX_FILE_SIZE``,
       reject with 400 BEFORE consuming any payload.
    2. Otherwise stream ``MAX_FILE_SIZE + 1`` bytes; if the trailing byte
       arrived, the body is over the limit and we fail loudly. This bounds
       the per-request RAM footprint regardless of a missing or lying
       Content-Length header.
    """
    declared = getattr(image, "size", None)
    if isinstance(declared, int) and declared > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB",
        )
    contents = await image.read(MAX_FILE_SIZE + 1)
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB",
        )
    return contents


def _open_image(contents: bytes) -> Image.Image:
    """Decode bytes into a validated, EXIF-normalised RGB PIL image."""
    try:
        img = Image.open(io.BytesIO(contents))
        # Reject oversized resolutions from the HEADER, before the full
        # decode: Pillow only hard-errors at 2× MAX_IMAGE_PIXELS, so a
        # ~36 MP upload would otherwise fully decode (~150 MB RSS) just
        # to be bounced by the post-decode dimension check below.
        w, h = img.size
        if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
            raise HTTPException(
                status_code=400,
                detail=f"Image resolution too large ({w}x{h}). "
                       f"Maximum dimension: {MAX_IMAGE_DIMENSION}px",
            )
        img.load()  # force actual decoding to surface truncation errors
        # Apply the EXIF orientation tag BEFORE converting to RGB.
        # Phone cameras commonly store sideways/upside-down captures
        # with an orientation flag rather than rotating pixels; PIL drops
        # EXIF on ``convert("RGB")``, so without this the detector and
        # classifier see a rotated leaf and lose accuracy on portrait
        # shots. ``exif_transpose`` is a no-op when no tag is present.
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=400, detail="Image exceeds decompression safety limit") from exc
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc


def _sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest. Pulled out so the analyze handler can run it in
    a thread — hashing up to MAX_UPLOAD_BYTES on the event loop would stall
    every other request for the duration."""
    return hashlib.sha256(data).hexdigest()


# ── Scan recording (anonymized model-improvement loop) ──────────────


def _anonymise_scan_image(img: Image.Image) -> tuple[bytes, str, str | None]:
    """Re-encode a decoded PIL image for labeling storage: downscale to
    ``SCAN_IMG_MAX_SIDE``, JPEG q88 WITHOUT exif — all EXIF/GPS metadata is
    dropped by construction. Returns ``(jpeg_bytes, sha256, phash)``."""
    img = img.convert("RGB")
    if max(img.size) > SCAN_IMG_MAX_SIDE:
        img.thumbnail((SCAN_IMG_MAX_SIDE, SCAN_IMG_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)  # no exif= ⇒ metadata stripped
    body = buf.getvalue()
    return body, _sha256_hex(body), labeling.phash_bytes(body)


def _scan_keep_sampled(sha: str, rate: float) -> bool:
    """Deterministic (by sha) keep decision for confident common-class scans."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return int(sha[:8], 16) % 10_000 < int(rate * 10_000)


def _record_scan_candidate(
    img: Image.Image, scan_id: str, class_name: str,
    confidence: float, pset_classes: list[str] | None,
) -> None:
    """Record one anonymized production scan into the labeling store
    (post-response via BackgroundTasks — zero hot-path latency). Sync and
    fail-soft: recording must never break or slow an analyze response.

    Class-balanced caps keep the corpus useful: suspicious (``pending``)
    scans and weak-class (target>0) scans are always kept; confident scans of
    common classes are sampled at ``SCAN_COMMON_KEEP_RATE``; a per-class
    daily cap bounds disk regardless."""
    try:
        if not SCAN_RECORDING_ENABLED or class_name == "not_a_plant":
            # The blur gate emits a synthetic not_a_plant (blurry_image
            # warning), so this single check excludes NAP + blur-gated scans.
            return
        try:
            from .labeling_auto import route_scan
        except ImportError:  # pragma: no cover - script mode
            from labeling_auto import route_scan
        status = route_scan(
            class_name, confidence, pset_classes,
            trust_conformal=LABELING_TRUST_CONFORMAL, conf_high=LABELING_CONF_HIGH,
        )
        body, sha, ph = _anonymise_scan_image(img)
        # Always keep model-uncertain scans (pending) and every disease class;
        # only the abundant easy class (healthy) is sampled down.
        if (
            status == "auto_yes" and class_name not in DISEASE_CLASSES
            and not _scan_keep_sampled(sha, SCAN_COMMON_KEEP_RATE)
        ):
            return
        if labeling.count_user_scans_today(class_name) >= SCAN_DAILY_CAP_PER_CLASS:
            return
        fn = f"{sha}.jpg"
        labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
        (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
        labeling.insert_candidate(
            cls=class_name, image_path=fn, source="user_scan", source_url=None,
            subject_plant=None, sha=sha, phash=ph, status=status,
            model_pred=class_name, model_conf=float(confidence or 0.0),
            model_set=list(pset_classes or []), scan_id=scan_id,
        )
    except Exception as exc:  # noqa: BLE001 - never raise from a background task
        logger.warning("Scan recording failed for %s: %s", scan_id[:16], exc)


# ── Endpoints ────────────────────────────────────────────────────────

api_router = APIRouter(prefix="/api")

@api_router.get("/health")
async def health_check():
    """Health check — server status, uptime, request count."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    return {
        "status": "ok",
        "version": APP_VERSION,
        "num_classes": len(pipeline.classifier.CLASS_NAMES),
        "uptime_seconds": round(uptime, 1),
        "total_requests": _snapshot_metrics()["total_requests"],
    }


# The two physiological-stress classes whose care advice genuinely
# diverges — under-watering (``leaf_scorch_underwatering``) says "water me"
# / fix humidity & salts, nutrient deficiency says "feed me". When the
# conformal set can't separate them (the kept-both taxonomy decision), the
# result screen shows BOTH care cards instead of forcing the user to guess.
_PHYSIOLOGICAL_CARE_PAIR = frozenset(
    {"leaf_scorch_underwatering", "nutrient_deficiency"}
)


def _compact_care_card(cls: str) -> dict:
    """Bilingual care card for one class, used in the dual-care prediction
    set so the client can render a second full care card without a second
    round-trip."""
    info = DISEASES_DATABASE.get(cls, DISEASES_DATABASE["unknown"])
    return {
        "class": cls,
        "name_en": info.get("name_en", cls.replace("_", " ").title()),
        "name_ru": info.get("name_ru", cls),
        "description": info.get("description_en", ""),
        "description_ru": info.get("description_ru", ""),
        "treatment": info.get("treatment_en", []),
        "treatment_ru": info.get("treatment_ru", []),
        "prevention": info.get("prevention_en", []),
        "prevention_ru": info.get("prevention_ru", []),
    }


def _attach_dual_care(prediction_set: dict) -> None:
    """Attach both physiological care cards in-place (P0(b)).

    No-op unless the conformal set contains BOTH ``leaf_scorch_underwatering``
    and ``nutrient_deficiency`` — the one ambiguous pair whose care advice
    diverges enough to warrant showing two cards. Scoped this tightly so
    every other prediction set keeps a lean payload.
    """
    classes = prediction_set.get("classes") or []
    if not _PHYSIOLOGICAL_CARE_PAIR.issubset(set(classes)):
        return
    prediction_set["dual_care"] = True
    prediction_set["care_cards"] = [
        _compact_care_card(cls)
        for cls in classes
        if cls in _PHYSIOLOGICAL_CARE_PAIR
    ]


@api_router.get("/capabilities")
async def capabilities():
    """Model-family availability for the scan-screen picker.

    The client calls this once when opening the picker to decide which
    tiers to offer. It greys out / blocks any family reported unavailable —
    notably ``frontier`` when ``FRONTIER_ENABLED`` is false or its bundle
    failed to load. ``aqua`` is always offered because it runs on-device
    (its real capability depends on the app shipping the tflite student).
    Tolerates a not-yet-ready pipeline so the app can call it during boot.
    """
    base_ready = pipeline is not None
    families = [
        {
            "id": "aqua",
            "available": True,
            "location": "on_device",
            "model_family": "aqua",
        },
        {
            "id": "gamma",
            "available": base_ready,
            "location": "server",
            "model_family": pipeline.classifier.model_family if base_ready else "gamma",
        },
        {
            "id": "frontier",
            "available": _family_available("frontier"),
            "location": "server",
            "model_family": (
                pipeline_frontier.classifier.model_family
                if pipeline_frontier is not None else "frontier"
            ),
        },
    ]
    # Consolidated model/version metadata (formerly /version and /classes).
    # The int8 ship flag tells operators whether classifier_int8.onnx is genuine
    # INT8 or the FP32 ONNX renamed; defaults to false when the card is missing
    # or malformed.
    int8_shipped = False
    int8_artifact: str | None = None
    try:
        with open(MODELS_DIR / "model_card.json", encoding="utf-8") as fh:
            _int8 = (json.load(fh).get("int8") or {})
        int8_shipped = bool(_int8.get("shipped", False))
        _artifact = _int8.get("shipped_artifact")
        if isinstance(_artifact, str):
            int8_artifact = _artifact
    except (OSError, ValueError):
        pass

    model: dict | None = None
    model_version: str | None = None
    classes_list: list[dict] = []
    if base_ready:
        clf = pipeline.classifier
        model_version = getattr(clf, "meta", {}).get("model_version")
        model = {
            "classifier_name": "plantvision",
            "model_family": clf.model_family,
            "detector_arch": "YOLOv8n (single-class plant_leaf, indoor-trained)",
            "classifier_arch": (
                f"PlantVision {clf.architecture} @ "
                f"{clf.img_size} px / {clf.backend} backend"
            ),
            "stages": 2,
            "focus": "houseplants",
            "int8_shipped": int8_shipped,
            "int8_artifact": int8_artifact,
        }
        for name in clf.CLASS_NAMES:
            info = DISEASES_DATABASE.get(name, {})
            classes_list.append({
                "class": name,
                "name_en": info.get("name_en", name.replace("_", " ").title()),
                "name_ru": info.get("name_ru", name),
                "is_healthy": bool(info.get("is_healthy", False)),
            })

    return {
        "default": DEFAULT_FAMILY,
        "families": families,
        "version": APP_VERSION,
        "server_version": APP_VERSION,
        "model_version": model_version,
        "model": model,
        "classes": classes_list,
    }


@api_router.get("/metrics")
async def metrics():
    """Operational metrics (JSON). Useful for dashboards and jury demos."""
    snap = _snapshot_metrics()
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    return {
        "uptime_seconds": round(uptime, 1),
        **snap,
        "ai_doctor": chat_metrics_snapshot(),
        "feedback": feedback_counts(),
        "community": community_stats_snapshot(),
    }


_PROM_LABEL_ESCAPE = str.maketrans({"\\": r"\\", "\"": r"\"", "\n": r"\n"})


def _prom_label(value: str) -> str:
    return value.translate(_PROM_LABEL_ESCAPE)


@api_router.get("/metrics/prometheus")
async def metrics_prometheus():
    """Prometheus text exposition (v0.0.4) of the same counters as /api/metrics."""
    snap = _snapshot_metrics()
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    version = _prom_label(APP_VERSION)

    lines = [
        "# HELP plantdiseases_uptime_seconds Seconds since the server started.",
        "# TYPE plantdiseases_uptime_seconds gauge",
        f"plantdiseases_uptime_seconds {uptime:.3f}",
        "# HELP plantdiseases_requests_total Total HTTP requests handled.",
        "# TYPE plantdiseases_requests_total counter",
        f"plantdiseases_requests_total {snap['total_requests']}",
        "# HELP plantdiseases_analyses_total Total successful /api/analyze responses.",
        "# TYPE plantdiseases_analyses_total counter",
        f"plantdiseases_analyses_total {snap['total_analyses']}",
        "# HELP plantdiseases_errors_total Total error responses from /api/analyze.",
        "# TYPE plantdiseases_errors_total counter",
        f"plantdiseases_errors_total {snap['total_errors']}",
        "# HELP plantdiseases_analyze_latency_ms_avg Average analyse latency in milliseconds.",
        "# TYPE plantdiseases_analyze_latency_ms_avg gauge",
        f"plantdiseases_analyze_latency_ms_avg {snap['avg_latency_ms']}",
        "# HELP plantdiseases_build_info Build version label.",
        "# TYPE plantdiseases_build_info gauge",
        f'plantdiseases_build_info{{version="{version}"}} 1',
        "# HELP plantdiseases_class_predictions_total Count of predictions per class.",
        "# TYPE plantdiseases_class_predictions_total counter",
    ]
    for class_name, count in sorted(snap["class_counts"].items()):
        lines.append(
            f'plantdiseases_class_predictions_total{{class="{_prom_label(class_name)}"}} {count}'
        )

    body = "\n".join(lines) + "\n"
    return StarletteResponse(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


async def _species_identify_best_effort(
    image_bytes: bytes, *, timeout: float, lang: str = "en",
) -> dict | None:
    """Run PlantNet identify on the default thread pool with a tight
    timeout (NOT the single-worker inference executor — there it would
    serialise behind / ahead of the pipeline forward instead of running
    in parallel with it). Returns the compact species block ({class, score,
    scientific, common, source}) for the response, or ``None`` if the
    call failed, timed out, or PlantNet isn't configured.

    ``lang`` is forwarded so the returned ``common_name`` is
    localised — without it the carousel shows "Rubber tree" even on
    a Russian-locale device.

    Never raises — every failure mode is logged at INFO and folded
    into ``None`` so the analyze response stays whole.
    """
    loop = asyncio.get_running_loop()
    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                # identify_species keyword args (organ, lang) are
                # keyword-only — run_in_executor only takes positional
                # args, so we wrap in a lambda. Passing positionally
                # raises TypeError at call time and surfaces as a 500.
                lambda: identify_species(image_bytes, organ="auto", lang=lang),
            ),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.CancelledError):
        logger.info("Species identify skipped (timeout / cancelled)")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("Species identify failed: %s", exc)
        return None
    if not raw or not raw.get("available"):
        return {
            "available": False,
            "source": raw.get("source", "unavailable") if raw else "unavailable",
            "best": None,
            "candidates": [],
        }
    candidates = raw.get("results") or []
    if not candidates:
        return {
            "available": True,
            "source": raw.get("source", "plantnet"),
            "best": None,
            "candidates": [],
        }
    def _shape(entry: dict) -> dict:
        # PlantNet returns a `common_names` list; we surface only the
        # first one for compactness. The full list stays available via
        # the dedicated /api/identify endpoint when needed.
        common_names = entry.get("common_names") or []
        return {
            "scientific_name": entry.get("scientific_name"),
            "common_name": (common_names[0] if common_names else None),
            "score": round(float(entry.get("score", 0.0)), 4),
        }
    return {
        "available": True,
        "source": raw.get("source", "plantnet"),
        "best": _shape(candidates[0]),
        "candidates": [_shape(c) for c in candidates[:3]],
    }


@api_router.post("/analyze")
async def analyze_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    species: str | None = Query(default=None, max_length=128),
    locale: str = Query(default="en", max_length=16),
    family: str = Query(default=DEFAULT_FAMILY, max_length=16),
):
    """Analyse a plant image for diseases (two-stage pipeline).

    Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
    Returns bilingual (EN/RU) disease diagnosis with treatment advice,
    detected region (with severity score) and the top-k class
    probabilities. ``locale`` (default "en") is forwarded to PlantNet
    so the species ``common_name`` is localised in the response.

    ``family`` selects the model tier (aqua | gamma | frontier). ``gamma``
    is the default; ``aqua`` is normally answered on-device but is served
    by gamma here for online parity; ``frontier`` requires the operator to
    have enabled and provisioned it (otherwise a graceful ``available:false``
    payload is returned, never a 5xx).
    """
    if pipeline is None:
        _record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")

    family = _normalise_family(family)
    # Frontier gate: short-circuit BEFORE doing any decode/inference work
    # when the family isn't available on this server. Mirrors the optional-
    # feature pattern (PlantNet / chat): HTTP 200 with ``available:false``
    # so the client renders "unavailable" instead of treating it as a hard
    # error. The app additionally checks /api/capabilities up front to grey
    # the option out, so this is the defensive backstop.
    if family == "frontier" and not _family_available("frontier"):
        return JSONResponse(content={
            "available": False,
            "family": "frontier",
            "reason": "frontier_disabled",
            "message": (
                "Frontier inference is not enabled on this server. "
                "Pick Gamma for an online diagnosis, or Aqua for offline."
            ),
        })
    serving_pipeline = _pipeline_for_family(family) or pipeline

    start_time = time.time()

    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        _record_error()
        logger.warning("Rejected file with content_type=%s", image.content_type)
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Allowed: JPEG, PNG, WebP, BMP",
        )

    fname = _safe_filename(image.filename)
    try:
        contents = await _read_upload(image)
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        _record_error()
        logger.warning("Failed to read upload stream from %s: %s", fname, exc)
        raise HTTPException(status_code=400, detail="Could not read uploaded file") from exc

    loop = asyncio.get_running_loop()
    try:
        _validate_image_bytes(contents)
        # Decode off the event loop. A large JPEG decode (img.load()) can
        # block the loop for tens of ms — long enough to stall every other
        # in-flight request — so it runs on the default threadpool. The
        # cheap magic-byte check stays inline.
        img = await loop.run_in_executor(None, _open_image, contents)
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        # Guard against any non-HTTPException leaking from PIL — return a
        # consistent 400 instead of letting the request crash with 500.
        _record_error()
        logger.exception("Unexpected error while validating image %s: %s", fname, exc)
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc

    img_w, img_h = img.size
    if img_w > MAX_IMAGE_DIMENSION or img_h > MAX_IMAGE_DIMENSION:
        _record_error()
        logger.warning("Rejected oversized image: %dx%d from %s", img_w, img_h, fname)
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too large ({img_w}x{img_h}). "
                   f"Maximum dimension: {MAX_IMAGE_DIMENSION}px",
        )
    if min(img_w, img_h) < MIN_IMAGE_DIMENSION:
        _record_error()
        logger.warning("Rejected undersized image: %dx%d from %s", img_w, img_h, fname)
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too small ({img_w}x{img_h}). "
                   f"Minimum dimension: {MIN_IMAGE_DIMENSION}px — please retake the photo.",
        )

    # Same-photo short-circuit. The hash is over the raw upload bytes,
    # not the decoded pixels, so even a re-encode produces a fresh
    # entry (which is the right call: re-encoded photos can differ
    # under quantisation in ways that flip the verdict). Cached
    # responses are re-emitted byte-identical with two added fields:
    # ``is_repeat=true`` and ``cached_at`` so the client can render
    # a "you saw this one earlier" banner instead of pretending it's
    # a fresh diagnosis.
    #
    # The cache key is (image_hash, plantnet_lang). The ``?species=``
    # override drives toxicity lookup only — see the auto/explicit
    # fall-through ~80 lines below — so on a cache hit we re-resolve
    # toxicity against the override and patch the three affected
    # fields, leaving the verdict / detection / care-plan-relevant
    # fields untouched (they're properties of the image, not of the
    # user's species hint). The species block, however, IS locale-
    # dependent — PlantNet's ``common_name`` comes back in the
    # request's ``lang``. Without ``plantnet_lang`` in the cache key,
    # a Russian user's first upload poisons the cached species block
    # with Russian names for every later English-locale viewer of the
    # same image (and vice versa).
    plantnet_lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    image_sha = await loop.run_in_executor(None, _sha256_hex, contents)
    # Family is part of the cache key: gamma and frontier are different
    # models and can land on different verdicts for the same bytes, so a
    # gamma cache hit must never be replayed for a frontier request.
    cache_key = f"{image_sha}:{plantnet_lang}:{family}"
    cache_hit = _analyze_cache_get(cache_key)
    if cache_hit is not None:
        # Deep-copy so the toxicity-override patch below (and any
        # incidental mutation by downstream middleware) can't poison
        # the cached entry for the next reader. A shallow ``dict(...)``
        # only copies the top level — nested ``species.candidates`` /
        # ``top_k`` / ``detection.regions`` would still be aliased.
        cached_payload = copy.deepcopy(cache_hit["payload"])
        cached_payload["is_repeat"] = True
        cached_payload["cached_at"] = cache_hit["cached_at"]
        cached_payload["elapsed_ms"] = 0
        species_override = (species or "").strip() or None
        if species_override:
            overridden = lookup_pet_toxicity(species_override)
            cached_payload["toxic_to_pets"] = overridden["toxic_to_pets"]
            cached_payload["toxic_species_list"] = overridden["species_list"]
            cached_payload["toxicity_source"] = overridden["source"]
        return JSONResponse(content=cached_payload)

    # Fire the PlantNet species identify in parallel with the disease
    # pipeline. PlantNet is best-effort: if the call fails / times out
    # / the API key isn't configured, we still return a complete
    # diagnosis — the species field just lands as ``None``. The 6s cap
    # is much tighter than INFERENCE_TIMEOUT (30s) because PlantNet is
    # an external HTTP hop and a slow upstream shouldn't hold the
    # whole analyze response hostage. ``plantnet_lang`` was resolved
    # above for the cache key.
    species_task = asyncio.create_task(
        _species_identify_best_effort(contents, timeout=6.0, lang=plantnet_lang)
    )
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_get_inference_executor(), serving_pipeline.analyze, img),
            timeout=INFERENCE_TIMEOUT,
        )
    except TimeoutError as exc:
        species_task.cancel()
        _record_error()
        logger.error("Inference timed out after %.0fs for %s", INFERENCE_TIMEOUT, fname)
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
        species_task.cancel()
        _record_error()
        logger.exception("Inference crashed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal inference error") from exc

    class_name = result["class_name"]
    confidence = result["confidence"]
    all_probs = result.get("all_probs", {})
    warnings = list(result.get("warnings", []))
    top_k = list(result.get("top_k", []))
    disease_info = DISEASES_DATABASE.get(class_name, DISEASES_DATABASE["unknown"])

    elapsed = time.time() - start_time
    _record_analysis(class_name, elapsed * 1000)

    logger.info(
        "Analysis complete: class=%s confidence=%.3f time=%.2fs file=%s warnings=%s",
        class_name, confidence, elapsed, fname, warnings,
    )

    rounded_probs = {k: round(v, 4) for k, v in all_probs.items()}

    # Shannon-entropy uncertainty in [0, 1]; higher = more uncertain.
    # Guards: empty dict, single-class (log(1)=0) and degenerate
    # all-zero distributions all collapse to 0.0 instead of NaN.
    uncertainty = 0.0
    if len(rounded_probs) > 1:
        vals = [p for p in rounded_probs.values() if p > 0]
        if vals:
            entropy = -sum(p * math.log(p) for p in vals)
            max_entropy = math.log(len(rounded_probs))
            if max_entropy > 0:
                uncertainty = round(max(0.0, min(1.0, entropy / max_entropy)), 4)

    detection = result["detection"]

    # Annotate top_k with the bilingual display name so the client can
    # render "Leaf mould - 6%" (or its RU variant) without a second
    # lookup table.
    top_k_localised = []
    for entry in top_k:
        cls = entry.get("class", "")
        info = DISEASES_DATABASE.get(cls, DISEASES_DATABASE["unknown"])
        top_k_localised.append({
            "class": cls,
            "confidence": entry.get("confidence", 0.0),
            "name_en": info.get("name_en", cls.replace("_", " ").title()),
            "name_ru": info.get("name_ru", cls),
            "is_healthy": bool(info.get("is_healthy", False)),
        })

    # `is_healthy` mirrors the displayed class name so the client's
    # status badge can never contradict the diagnosis text. Uncertainty
    # is conveyed by the `warnings` array (`uncertain_healthy`,
    # `disease_suspected:<class>`) and the top-k alternatives card,
    # not by silently flipping this boolean.
    is_healthy_effective = bool(disease_info.get("is_healthy", False))

    # Conformal prediction set: classes the model can't rule out at the
    # configured coverage level. Annotated with bilingual names so the
    # UI can render "leaf_spot OR viral_disease @ 90%"
    # without a second lookup table. ``None`` when calibration data
    # isn't yet available — clients must treat it as optional.
    prediction_set = result.get("prediction_set")
    if prediction_set is not None:
        # ``advisory`` is True until the conformal calibration set
        # carries ≥ 50 samples per class. Today's per-class counts
        # (gray_mold_botrytis=5, leaf_scorch_underwatering=3,
        # powdery_mildew=5, not_a_plant=0 — see
        # ``server/models/model_card.json``) are too sparse for the
        # stated 90 % coverage to be statistically meaningful. The
        # client-facing flag tells the UI to render the set as
        # "alternative candidates" rather than "guaranteed coverage".
        prediction_set = {
            **prediction_set,
            "advisory": True,
            "items": [
                {
                    "class": cls,
                    "name_en": DISEASES_DATABASE.get(cls, DISEASES_DATABASE["unknown"]).get(
                        "name_en", cls.replace("_", " ").title()
                    ),
                    "name_ru": DISEASES_DATABASE.get(cls, DISEASES_DATABASE["unknown"]).get(
                        "name_ru", cls
                    ),
                }
                for cls in prediction_set["classes"]
            ],
        }
        # Dual care cards (P0(b), kept-both taxonomy): when the set can't
        # separate the two physiological-stress classes, their care advice
        # diverges enough ("water me" vs "feed me") that we surface BOTH
        # full care cards rather than only the top-1's.
        _attach_dual_care(prediction_set)

    # Resolve the species fan-out. We started it in parallel with the
    # disease pipeline so this normally returns immediately. If the
    # caller already supplied a ``species`` query param (e.g. user
    # picked one earlier), it overrides the auto-detected scientific
    # name when looking up pet toxicity, but we keep the auto-detected
    # block in the response either way so the client can show the
    # match confidence.
    species_payload: dict | None = None
    try:
        species_payload = await species_task
    except Exception:  # noqa: BLE001 — defensive; helper already swallows
        species_payload = None

    # PlantNet on a non-plant photo always returns a low-quality guess
    # ("Phaseolus vulgaris" because there was some green pixel noise)
    # which is actively misleading on the result screen. When the
    # pipeline lands on ``not_a_plant`` we suppress the species block
    # entirely — the LRU cache in ``identify_species`` keeps the API
    # call from being wasted on a repeat upload of the same image.
    if class_name == "not_a_plant":
        species_payload = {
            "available": False, "source": "skipped_nap",
            "best": None, "candidates": [],
        }

    # Pet-toxicity surface — resolves from the explicit ``species``
    # query param when supplied, otherwise falls back to the auto-
    # detected best match. Without either we return
    # ``source="unknown"`` and the client renders an "unconfirmed"
    # copy rather than falsely reassuring.
    auto_best = (species_payload or {}).get("best") or {}
    toxicity_species = species or auto_best.get("scientific_name")
    toxicity = lookup_pet_toxicity(toxicity_species) if toxicity_species else {
        "toxic_to_pets": False, "species_list": [], "source": "unknown",
    }

    # Mint the scan_id (it must ride in the response) and persist it to the
    # issued-ids log BEFORE returning. This previously ran fire-and-forget on
    # the executor to shave a SQLite round-trip off the response path, but
    # that raced the feedback gate: a client that POSTs feedback right after
    # the result lands could reach /api/feedback before the row was written,
    # and the gate would reject a legitimate scan_id. The write is a single
    # indexed INSERT executed off the event loop, so awaiting it costs ~ms
    # while removing the race entirely.
    scan_id = uuid.uuid4().hex
    await loop.run_in_executor(None, _persist_issued_scan_id, scan_id)

    response_payload = {
        # Canonical class key the pipeline landed on AFTER all
        # post-processing (weak-healthy reroute, OOD gate, etc.).
        # ``disease_name`` / ``disease_name_ru`` are the localised
        # display strings derived from this key. Clients should echo
        # ``class_name`` back as ``predicted_class`` on /api/feedback
        # — argmax(all_probs) is the PRE-reroute class and would
        # poison per-class accuracy metrics if used instead.
        "class_name": class_name,
        # Which model tier actually produced this verdict. Lets the client
        # confirm it got the family it asked for (and fall back gracefully if
        # a future server downgrades the request).
        "family": family,
        "disease_name": disease_info["name_en"],
        "disease_name_ru": disease_info["name_ru"],
        "confidence": round(confidence, 3),
        "description": disease_info["description_en"],
        "description_ru": disease_info["description_ru"],
        "treatment": disease_info["treatment_en"],
        "treatment_ru": disease_info["treatment_ru"],
        "prevention": disease_info["prevention_en"],
        "prevention_ru": disease_info["prevention_ru"],
        "is_healthy": is_healthy_effective,
        "detection": detection,
        "uncertainty": uncertainty,
        "severity": result.get("severity", "low"),
        "all_probs": rounded_probs,
        "top_k": top_k_localised,
        "warnings": warnings,
        "prediction_set": prediction_set,
        "toxic_to_pets": toxicity["toxic_to_pets"],
        "toxic_species_list": toxicity["species_list"],
        "toxicity_source": toxicity["source"],
        "species": species_payload,
        "elapsed_ms": result["elapsed_ms"],
        "server_version": APP_VERSION,
        # Set to true only when the response is replayed from the
        # SHA-256 cache above; first-time scans get false here.
        "is_repeat": False,
        # Server-minted scan_id for this response. Clients should
        # send this same string back on /api/feedback so the gate
        # in main.py can confirm the feedback ties to a real scan
        # we ran. Stored in the issued_scan_ids table; expires after
        # ISSUED_SCAN_ID_TTL_DAYS.
        "scan_id": scan_id,
    }
    # Stash the freshly-built response under its (image_hash,
    # plantnet_lang) key so the NEXT identical upload from the same
    # locale short-circuits at the top of this handler.
    _analyze_cache_put(cache_key, response_payload)
    # Record the scan (anonymized) for the model-improvement loop. Runs
    # AFTER the response is sent (BackgroundTasks) so it costs the user
    # nothing; cache hits return earlier and are never double-recorded.
    # ``img.copy()`` is defensive — the task must never share pixels with
    # anything the handler might still touch.
    if SCAN_RECORDING_ENABLED and class_name != "not_a_plant":
        background_tasks.add_task(
            _record_scan_candidate, img.copy(), scan_id, class_name,
            confidence, list((result.get("prediction_set") or {}).get("classes") or []),
        )
    return JSONResponse(content=response_payload)


# ── Smart APIs ──────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)
    locale: str = Field(default="en", max_length=8)
    diagnosis: dict | None = None


class CarePlanRequest(BaseModel):
    disease: str = Field(..., min_length=1, max_length=64)
    species: str | None = Field(default=None, max_length=128)
    light: str = Field(default="bright_indirect", max_length=32)
    locale: str = Field(default="en", max_length=8)
    # Optional season override; ``None`` auto-detects from today's date
    # (northern-hemisphere default). Accepted values: ``"growing"``
    # or ``"dormant"``.
    season: str | None = Field(default=None, max_length=16)


class SymptomDiagnoseRequest(BaseModel):
    # question_id -> option_id (see symptoms.SYMPTOM_WEIGHTS). Kept small and
    # length-capped — this is an untrusted client payload.
    answers: dict[str, str] = Field(default_factory=dict)
    locale: str = Field(default="en", max_length=8)

    @field_validator("answers")
    @classmethod
    def _validate_answers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("too many answers")
        for k, v in value.items():
            if len(k) > 32 or len(v) > 32:
                raise ValueError("answer id too long")
        return value


# ── Community scan corpus ───────────────────────────────────────────


# Region-string alphabet (country name / code + city / admin area).
# A region char is any Unicode letter (str.isalpha() is Unicode-aware, so
# Cyrillic / accented Latin / CJK localities like "Москва", "Köln", "東京" all
# pass) or digit, plus apostrophe + period + hyphen + space, which cover the
# bulk of place names ("Saint-John's", "Plymouth, U.K." trims to "Plymouth").
# Commas are deliberately excluded — country and locality are separate fields,
# so callers never embed a comma — and control chars / injection punctuation
# (";", "\n", …) still get filtered out.
def _region_char_ok(ch: str) -> bool:
    return ch.isalpha() or ch.isdigit() or ch in " .'-"


def _normalise_country(raw: str | None) -> str:
    """Sanitise the top-level country bucket.

    Accepts the new explicit country form ("GB", "Germany") and the
    legacy "City, Country" composite that older clients still send —
    in the latter case we keep the segment after the last comma. The
    result is trimmed, charset-checked and capped at 80 chars; an
    empty string means "rejected".
    """
    if not raw:
        return ""
    if "," in raw:
        raw = raw.rsplit(",", 1)[-1]
    cleaned = raw.strip()[:80]
    if not cleaned or any(not _region_char_ok(ch) for ch in cleaned):
        return ""
    # Canonicalize to an ISO-3166 alpha-2 code so "RU" / "ru" / "Russia" /
    # "Россия" all collapse into one bucket — otherwise the same country
    # splits across locale-specific representations and the popular / nearby
    # aggregation (and the curated ``popular_for`` map, which only matches
    # 2-letter codes) disagree per locale.
    return to_iso_alpha2(cleaned)


def _normalise_locality(raw: str | None) -> str:
    """Sanitise the sub-country city / admin-area string.

    Returns the cleaned value or "" when the caller didn't send a
    locality (country-only writes are legal — the corpus simply
    skips the second hierarchy level).
    """
    if not raw:
        return ""
    cleaned = raw.strip()[:80]
    if not cleaned or any(not _region_char_ok(ch) for ch in cleaned):
        return ""
    return cleaned


# There is no "worldwide" bucket any more — every scope is a real country.
# When an IP can't be located at all (private/VPN/no .mmdb) we fall back to
# this primary-market country so the feed + carousels still have a scope.
# Legacy sentinel still present in old DB rows; only used to scrub them on read.
_GLOBAL_COUNTRY = "GLOBAL"

# EN/RU display names keyed by ISO-3166 alpha-2 now live in ``countries.py``
# (shared with ``community.py``), alongside ``to_iso_alpha2`` which collapses a
# code / English name / localized name to one canonical bucket. Imported above
# as ``COUNTRY_NAMES``.


def _country_display(country: str | None) -> dict:
    code = (country or "").strip().upper()
    if not code or code == _GLOBAL_COUNTRY:
        # No location (community off / permission denied) → worldwide.
        return {
            "country": None,
            "country_name_en": "Worldwide",
            "country_name_ru": "по всему миру",
            "scope": "global",
        }
    en, ru = COUNTRY_NAMES.get(code, (country or code, country or code))
    return {
        "country": code if len(code) == 2 else country,
        "country_name_en": en,
        "country_name_ru": ru,
        "scope": "country",
    }


def _localize_item_country(row: dict) -> None:
    """Annotate one community-feed row with localized country *names* and
    scrub the internal ``GLOBAL`` bucket sentinel.

    Each row stores ``country`` as an ISO alpha-2 code (e.g. ``"PL"``) — the
    raw code is useless to a reader, so the feed cards used to print "PL".
    We attach ``country_name_en`` / ``country_name_ru`` (so the client can
    show "Poland" / "Польша" per locale) and blank the ``GLOBAL`` sentinel
    that detection-failed uploads carry, since it isn't a real place.
    """
    code = str(row.get("country") or "").strip().upper()
    if not code or code == _GLOBAL_COUNTRY:
        row["country"] = ""
        row["country_name_en"] = None
        row["country_name_ru"] = None
        return
    en, ru = COUNTRY_NAMES.get(code, (row.get("country"), row.get("country")))
    row["country_name_en"] = en
    row["country_name_ru"] = ru


def _valid_coord(value, limit: float) -> float | None:
    """Parse a client-sent coordinate, range-check it, and round to ~1 km so a
    precise position never lands in the corpus (coarse-location privacy)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) > limit:  # NaN or out of range
        return None
    return round(f, 2)


def _client_location(
    country: str | None = None,
    region: str | None = None,
    lat=None,
    lon=None,
) -> dict:
    """Normalize the client-supplied coarse location. There is no server-side
    IP geolocation any more — the app sends what it resolved from
    ``ACCESS_COARSE_LOCATION`` (+ an on-device reverse-geocode). Any field may
    be empty (community off / permission denied) → the caller falls back to the
    worldwide scope.
    """
    # ``_normalise_country`` already canonicalizes to an ISO alpha-2 code.
    c = (_normalise_country(country) if country else "")
    city = _normalise_locality(region) if region else ""
    la = _valid_coord(lat, 90.0)
    lo = _valid_coord(lon, 180.0)
    if la is None or lo is None:  # coords only count as a pair
        la = lo = None
    return {"country": c or None, "city": city or None, "lat": la, "lon": lo}


def _normalise_nickname(raw: str | None) -> str | None:
    """Sanitise a community nickname: drop non-printable chars (keeps unicode
    letters so Russian handles survive), trim, cap at 48. Empty → None."""
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ch.isprintable()).strip()[:48]
    return cleaned or None


class CommunityScanRequest(BaseModel):
    """Upload metadata for a single community-shared scan. The image
    bytes ride on a separate multipart field on the upload endpoint
    so a thin client never has to base64-encode anything.

    Hierarchy: ``country`` is the canonical top bucket (ISO code or
    country name); ``region`` is the optional sub-country locality
    (city, admin area). Older clients that still send the legacy
    composite as ``region`` get auto-split — the country tail lands
    in ``country`` and the leading segment in ``region``.
    """

    scan_id: str = Field(..., min_length=8, max_length=64)
    user_pseudo_id: str = Field(..., min_length=8, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=80)
    region: str = Field(default="", max_length=80)
    disease: str = Field(..., min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    species: str | None = Field(default=None, max_length=128)
    species_score: float | None = Field(default=None, ge=0.0, le=1.0)
    locale: str | None = Field(default=None, max_length=8)
    nickname: str | None = Field(default=None, max_length=64)

    @field_validator("nickname")
    @classmethod
    def _clean_nickname(cls, v: str | None) -> str | None:
        return _normalise_nickname(v)

    @field_validator("scan_id", "user_pseudo_id")
    @classmethod
    def _safe_token(cls, v: str) -> str:
        if any(ch not in _SCAN_ID_ALLOWED for ch in v):
            raise ValueError("invalid characters in id; expected [A-Za-z0-9_-]")
        return v

    @field_validator("country")
    @classmethod
    def _safe_country(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _normalise_country(v)
        if not cleaned:
            raise ValueError(
                "country must be a country name or ISO code (no control chars)"
            )
        return cleaned

    @field_validator("region")
    @classmethod
    def _safe_region(cls, v: str) -> str:
        # Empty is legal — country-only writes are supported.
        cleaned = _normalise_locality(v)
        if v and not cleaned:
            raise ValueError("region contains invalid characters")
        return cleaned

    @field_validator("disease")
    @classmethod
    def _known_disease(cls, v: str) -> str:
        cleaned = v.strip().lower()
        # ``not_a_plant`` and the deferred physiological classes are
        # legal community-share values too — they're still useful
        # signals for the retraining loop.
        if cleaned not in _FEEDBACK_ALLOWED_CORRECTIONS:
            raise ValueError(
                f"disease must be one of {sorted(_FEEDBACK_ALLOWED_CORRECTIONS)}"
            )
        return cleaned

    @field_validator("locale")
    @classmethod
    def _normalise_locale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()[:8]
        return cleaned or None


class ProgressionRequest(BaseModel):
    """Time-ordered list of scans (oldest first) for the disease-
    progression sparkline + trend the plant-detail screen renders."""

    history: list[dict]


# Classes the production head can actually predict. Tracks the
# ``class_names`` declaration in ``models/classes.json`` via
# ``classifier.DEFAULT_CLASS_NAMES`` so we only have one source of
# truth — feedback's ``out_of_head`` flag falls out the moment a
# correction lands outside this set.
try:
    from .classifier import DEFAULT_CLASS_NAMES as _HEAD_CLASS_NAMES
except ImportError:
    from classifier import DEFAULT_CLASS_NAMES as _HEAD_CLASS_NAMES

_FEEDBACK_IN_HEAD_CLASSES: frozenset[str] = frozenset(_HEAD_CLASS_NAMES)

# Out-of-head corrections — physiological classes the
# v2.2 head doesn't carry yet, plus a free-form catch-all. We accept
# them on /api/feedback (otherwise the user has no way to tell us
# "your model can't say this") and tag each row with ``out_of_head=1``
# so retraining can prioritise expanding the label set.
# ``leaf_scorch_underwatering`` and ``nutrient_deficiency`` moved
# into the head set (train_notebook.ipynb §1) and now arrive via
# ``_FEEDBACK_IN_HEAD_CLASSES`` instead.
_FEEDBACK_OUT_OF_HEAD_CLASSES: frozenset[str] = frozenset({
    "edema",
    "sunburn_or_scorch",
    "other",
})

_FEEDBACK_ALLOWED_CORRECTIONS: frozenset[str] = (
    _FEEDBACK_IN_HEAD_CLASSES | _FEEDBACK_OUT_OF_HEAD_CLASSES
)

_FEEDBACK_ALLOWED_REASONS: frozenset[str] = frozenset({
    "wrong_class",
    "low_quality_image",
    "occluded",
    "multiple_plants",
    "ambiguous_symptoms",
    "other",
})

_FEEDBACK_ALLOWED_PLATFORMS: frozenset[str] = frozenset({
    "android",
    "ios",
    "web",
    "test",
    "unknown",
})

# scan_id is a client-generated opaque token. Constrain it to a
# URL-safe alphabet of reasonable length so a runaway client can't
# stuff control bytes or megabyte blobs through the column. We don't
# enforce a strict UUID shape because legacy clients still mint
# numeric ids and we want them to keep working through the
# transition window.
_SCAN_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class FeedbackRequest(BaseModel):
    """Single user-feedback signal on a scan.

    The classifier is stateless, so the *scan* lives only on the
    client. ``scan_id`` is therefore client-generated and just has to
    be stable across the lifetime of one scan: we de-dupe by
    (user_pseudo_id, scan_id) downstream.

    Required: ``scan_id``, ``correct``.
    Optional: every other field — kept nullable so clients can roll
    out the richer payload progressively. Length bounds protect the
    append-only SQLite file from runaway clients.
    """

    scan_id: str = Field(..., min_length=8, max_length=64)
    correct: bool
    corrected_class: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=32)
    # Diagnosis context — what the model said when the user gave
    # feedback. Used to compute per-class accuracy without joining
    # against scan history (we don't have that history server-side).
    predicted_class: str | None = Field(default=None, max_length=64)
    predicted_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    top3: list[dict] | None = Field(default=None, max_length=3)
    # Client context — used for cohort analysis and abuse detection.
    model_version: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, max_length=8)
    platform: str | None = Field(default=None, max_length=16)
    # Opaque per-install id (UUIDv4 from the client). Lets us correlate
    # multiple feedback signals from one user *without* knowing who
    # they are.
    user_pseudo_id: str | None = Field(default=None, min_length=8, max_length=64)
    # Detector context — useful for separating crop-quality issues
    # from genuine classifier errors.
    region_count: int | None = Field(default=None, ge=0, le=64)
    primary_region_index: int | None = Field(default=None, ge=-1, le=63)
    # Wall-clock milliseconds between original scan and this feedback
    # tap. Capped at 30 days so a malicious client can't pretend the
    # signal is from the distant future / past.
    feedback_latency_ms: int | None = Field(default=None, ge=0, le=30 * 24 * 3600 * 1000)
    # Optional SHA-256 of the original image (hex) — lets us join
    # against any future image-side analytics without the image bytes
    # leaving the device.
    image_hash: str | None = Field(default=None, min_length=32, max_length=128)
    # Conformal prediction set the server returned with the original
    # /api/analyse response (the classes the model couldn't rule out
    # at the configured coverage). Persisting it on the feedback row
    # lets us answer "was the correct answer in the 90 % set?" — a
    # cleaner diagnostic than top1-only accuracy because it
    # separates calibration failures from point-estimate failures.
    # Capped at 16 keys / 64 chars each as a sanity guard.
    prediction_set: list[str] | None = Field(default=None, max_length=16)

    @field_validator("scan_id", "user_pseudo_id")
    @classmethod
    def _safe_token(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if any(ch not in _SCAN_ID_ALLOWED for ch in v):
            raise ValueError("invalid characters in id; expected [A-Za-z0-9_-]")
        return v

    @field_validator("corrected_class")
    @classmethod
    def _known_class(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if cleaned not in _FEEDBACK_ALLOWED_CORRECTIONS:
            raise ValueError(
                f"corrected_class must be one of "
                f"{sorted(_FEEDBACK_ALLOWED_CORRECTIONS)}; got {v!r}"
            )
        return cleaned

    @field_validator("predicted_class")
    @classmethod
    def _normalise_predicted(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        return cleaned or None

    @field_validator("reason")
    @classmethod
    def _known_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if cleaned not in _FEEDBACK_ALLOWED_REASONS:
            raise ValueError(
                f"reason must be one of {sorted(_FEEDBACK_ALLOWED_REASONS)}; got {v!r}"
            )
        return cleaned

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if cleaned not in _FEEDBACK_ALLOWED_PLATFORMS:
            # Unknown platform tags get folded to "unknown" rather
            # than rejected — we'd rather collect the signal than
            # bounce a client with a typo in BuildConfig.
            return "unknown"
        return cleaned

    @field_validator("locale")
    @classmethod
    def _normalise_locale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()[:8]
        return cleaned or None

    @field_validator("image_hash")
    @classmethod
    def _hex_only(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if any(ch not in "0123456789abcdef" for ch in cleaned):
            raise ValueError("image_hash must be lowercase hex")
        return cleaned

    @field_validator("top3")
    @classmethod
    def _shape_top3(cls, v: list[dict] | None) -> list[dict] | None:
        if not v:
            return None
        cleaned: list[dict] = []
        for entry in v[:3]:
            if not isinstance(entry, dict):
                continue
            cls_key = str(entry.get("class", "")).strip().lower()[:64]
            try:
                prob = float(entry.get("prob", 0.0))
            except (TypeError, ValueError):
                continue
            if not cls_key:
                continue
            cleaned.append({"class": cls_key, "prob": max(0.0, min(1.0, prob))})
        return cleaned or None


@api_router.post("/identify")
async def identify_endpoint(
    image: UploadFile = File(...),
    organ: str = "leaf",
    locale: str = "en",
):
    """PlantNet-backed plant species identification.

    Returns up to 5 candidate species with confidence scores. When the
    PlantNet API key is missing we still respond 200 OK with
    ``available: false`` so the client can render a graceful state
    instead of an error toast. Cached server-side by SHA-256(image) +
    organ + locale to keep the daily PlantNet quota intact.

    ``locale`` is forwarded to PlantNet's ``lang`` query param so the
    common names come back localised (e.g. "Каучуковое дерево" on
    ru-RU instead of "Rubber tree").
    """
    if pipeline is None:
        _record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")
    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        _record_error()
        raise HTTPException(status_code=400, detail="Unsupported image format")
    try:
        contents = await _read_upload(image)
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        _record_error()
        logger.warning("Failed to read upload stream for /api/identify: %s", exc)
        raise HTTPException(status_code=400, detail="Could not read uploaded file") from exc
    try:
        _validate_image_bytes(contents)
    except HTTPException:
        _record_error()
        raise
    organ = (organ or "auto").strip().lower()
    if organ not in ("leaf", "flower", "fruit", "bark", "habit", "auto"):
        organ = "auto"
    plantnet_lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: identify_species(contents, organ=organ, lang=plantnet_lang),
    )
    # Deep-copy result before merging so a cache-hit return from
    # ``identify_species`` (which now also deep-copies, but defence in
    # depth is cheap) doesn't share its nested ``results`` /
    # ``common_names`` lists with the JSON response — middleware mutation
    # would otherwise corrupt the PlantNet LRU entry for the next reader.
    return JSONResponse(content={**copy.deepcopy(result), "organ": organ})


@api_router.post("/chat")
async def chat_endpoint(payload: ChatRequest, request: Request):
    """AI Plant Doctor — OpenRouter chat completion (default model
    ``openai/gpt-oss-120b``).

    Per-session turn + token caps prevent budget overrun. Per-IP rate
    limiting from the global middleware still applies.
    """
    client_ip = _client_ip(request)
    # Pydantic doesn't bound dict size; reject oversize diagnosis blobs
    # before they hit chat_reply so a malicious client can't push a
    # 10 MB nested dict through the LLM context window.
    diagnosis = payload.diagnosis
    if diagnosis is not None and len(json.dumps(diagnosis, default=str)) > 8 * 1024:
        raise HTTPException(status_code=400, detail="diagnosis context too large")
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: chat_reply(
            session_id=payload.session_id,
            user_message=payload.message,
            locale=payload.locale,
            diagnosis_context=diagnosis,
        ),
    )
    # Abuse logging: cap-rejection from a specific IP is the
    # signature pattern for a runaway client. Log at WARNING so
    # ops dashboards can alert on it.
    if (
        not result.get("available")
        and result.get("reason") in ("session_turn_cap", "session_token_cap")
    ):
        logger.warning(
            "AI Doctor budget cap hit  ip=%s session=%s reason=%s",
            client_ip, payload.session_id, result["reason"],
        )
    return JSONResponse(content=result)


@api_router.post("/care-plan")
async def care_plan_endpoint(payload: CarePlanRequest):
    """Build a watering / fertilising / humidity schedule for a plant.

    Always returns a usable rules-based plan; when an OpenRouter key
    is configured the response also includes a Markdown ``summary_md``
    polished by the LLM. Disease must be one of the known classifier
    classes (``unknown`` is normalised to a generic monitoring plan).
    """
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")
    disease = payload.disease.strip()
    if disease not in DISEASES_DATABASE and disease != "unknown":
        # Don't reject — just normalise unknown to ``unknown``. Keeps the
        # endpoint forgiving while still producing a useful plan.
        disease = "unknown"
    plan = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: generate_care_plan(
            disease=disease,
            species=payload.species,
            light=payload.light,
            locale=payload.locale,
            season=payload.season,
        ),
    )
    toxicity = lookup_pet_toxicity(payload.species) if payload.species else {
        "toxic_to_pets": False, "species_list": [], "source": "unknown",
    }
    plan = {
        **plan,
        "toxic_to_pets": toxicity["toxic_to_pets"],
        "toxic_species_list": toxicity["species_list"],
        "toxicity_source": toxicity["source"],
    }
    return JSONResponse(content=plan)


@api_router.post("/symptom-diagnose")
async def symptom_diagnose_endpoint(payload: SymptomDiagnoseRequest):
    """No-photo symptom triage — the fallback when the camera/ML pipeline
    can't get a confident read.

    Scores the answered questionnaire against a transparent weight table
    (``symptoms.py``) and returns a ranked shortlist of likely conditions,
    each enriched with its full bilingual care card. ``low_confidence`` is
    set when the answers are too sparse/weak to trust, so the client can
    nudge the user to take a photo for a more reliable diagnosis. Always
    returns 200 with a usable payload — an empty questionnaire just yields
    an empty shortlist + the ``unknown`` guidance card.
    """
    triage = symptom_diagnose(payload.answers)
    results = []
    for item in triage["results"]:
        card = _compact_care_card(item["class"])
        card["score"] = item["score"]
        card["confidence"] = item["confidence"]
        card["rank"] = item["rank"]
        results.append(card)
    # When nothing scored, hand back the generic "unknown" guidance card so
    # the screen is never blank — and flag low confidence.
    if not results:
        fallback = _compact_care_card("unknown")
        fallback["score"] = 0.0
        fallback["confidence"] = 0.0
        fallback["rank"] = 1
        results = [fallback]
    return JSONResponse(
        content={
            "results": results,
            "answered": triage["answered"],
            "low_confidence": triage["low_confidence"] or not triage["results"],
        }
    )


@api_router.post("/progression")
async def progression_endpoint(payload: ProgressionRequest):
    """Disease-progression analysis. Returns a 5-point severity
    sparkline + a trend slope across the supplied history (oldest
    first). An empty list is allowed and yields a zero-padded
    baseline so the client can render an "awaiting data" state
    without special-casing."""
    return JSONResponse(content=compare_progression_history(payload.history))


@api_router.post("/feedback")
async def feedback_endpoint(payload: FeedbackRequest, request: Request):
    """Capture a thumbs-up / thumbs-down on a diagnosis, with full
    diagnostic + client context.

    Append-only — there is no update / delete path. The 1 req/s per-IP
    rate limit from the global middleware applies; the endpoint
    additionally caps ``note`` length via the pydantic model so a
    malicious client can't bloat the SQLite file with megabytes of
    text.

    The raw client IP is hashed (peppered SHA-256) before persistence
    so we never store PII at rest. The hash is stable across a single
    deployment, which is enough for abuse-rate detection.
    """
    client_ip = _client_ip(request)
    # Per-IP daily write quota. The rate limiter is in-memory and
    # resets on restart; this gate reads the same peppered IP-hash
    # column we persist on each row, so it survives across deploys.
    ip_hash = hash_client_ip(client_ip)
    today_writes = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: feedback_daily_writes(ip_hash),
    )
    if today_writes >= FEEDBACK_DAILY_QUOTA:
        logger.warning(
            "Feedback daily quota hit ip_hash=%s writes=%d cap=%d",
            ip_hash, today_writes, FEEDBACK_DAILY_QUOTA,
        )
        raise HTTPException(
            status_code=429,
            detail="Daily feedback quota reached. Try again tomorrow.",
        )

    # Bind the feedback to a scan_id we actually issued. Unknown
    # scan_ids are rejected — that's how we keep arbitrary clients
    # from poisoning the corrections corpus the retraining loop
    # consumes.
    scan_id_clean = payload.scan_id.strip()
    issued = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: is_scan_id_issued(scan_id_clean, ttl_days=ISSUED_SCAN_ID_TTL_DAYS),
    )
    if not issued:
        logger.warning(
            "Rejected feedback on unissued scan_id=%s ip_hash=%s",
            scan_id_clean[:16], ip_hash,
        )
        raise HTTPException(
            status_code=422,
            detail="Unknown scan_id — please re-scan before sending feedback.",
        )

    # Pydantic v2 has already normalised these — corrected_class is
    # constrained to the known class set, reason to the known reason
    # set, scan_id / user_pseudo_id to the URL-safe alphabet, etc.
    note = (payload.note or "").strip() or None
    top3_pairs = (
        [(entry["class"], entry["prob"]) for entry in payload.top3]
        if payload.top3 else None
    )

    # ``out_of_head`` only carries signal when the user supplied a
    # correction. For correct=1 rows or rows without a correction we
    # leave it NULL so retraining filters on ``IS 1`` rather than
    # treating absence as a no.
    out_of_head: bool | None = None
    if payload.corrected_class:
        out_of_head = payload.corrected_class not in _FEEDBACK_IN_HEAD_CLASSES
    try:
        row_id = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: record_feedback(
                scan_id=payload.scan_id.strip(),
                correct=payload.correct,
                corrected_class=payload.corrected_class,
                note=note,
                client_ip=client_ip,
                predicted_class=payload.predicted_class,
                predicted_confidence=payload.predicted_confidence,
                top3=top3_pairs,
                model_version=payload.model_version,
                app_version=payload.app_version,
                locale=payload.locale,
                platform=payload.platform,
                user_pseudo_id=payload.user_pseudo_id,
                region_count=payload.region_count,
                primary_region_index=payload.primary_region_index,
                feedback_latency_ms=payload.feedback_latency_ms,
                reason=payload.reason,
                image_hash=payload.image_hash,
                out_of_head=out_of_head,
                prediction_set=payload.prediction_set,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to record feedback: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to record feedback") from exc

    # Fold the feedback into the collection store (fail-soft, off the event
    # loop): negative feedback re-opens the recorded scan candidate as a
    # top-priority pending card; positive feedback stamps user_correct and
    # can promote a pending card to auto_yes (model+user agreement).
    def _mark_labeling_feedback() -> None:
        try:
            labeling.mark_feedback(
                scan_id_clean, payload.correct, payload.corrected_class, note,
            )
        except Exception as exc:  # noqa: BLE001 - feedback must still succeed
            logger.warning("labeling.mark_feedback failed: %s", exc)

    asyncio.get_running_loop().run_in_executor(None, _mark_labeling_feedback)

    # Truncate scan_id in the access log so attacker-supplied bytes
    # can't blow up log lines, even though the pydantic validator
    # already capped it at 64 chars.
    logger.info(
        "Feedback recorded id=%d scan=%s correct=%s predicted=%s corrected=%s reason=%s",
        row_id,
        payload.scan_id[:16],
        payload.correct,
        payload.predicted_class,
        payload.corrected_class,
        payload.reason,
    )
    return JSONResponse(content={"ok": True, "id": row_id})


@api_router.post("/feedback/image")
async def feedback_image_upload(
    request: Request,
    scan_id: str = Form(...),
    image: UploadFile = File(...),
):
    """Persist the original image bytes for a negative-feedback row.

    Called by the Android client immediately after a successful
    ``correct=false`` ``/api/feedback`` submission. The image is
    written to ``FEEDBACK_IMG_DIR`` keyed by SHA-256 of the bytes,
    and the most recent feedback row for ``scan_id`` is updated
    in-place with ``image_path``. Same per-IP rate limit and
    scan-id-was-issued gate as ``/api/feedback`` so a hostile
    client can't spray arbitrary bytes at the corpus.

    Privacy note: photo storage for model improvement is disclosed in
    the first-launch consent screen (and the feedback sheet copy); the
    image is also anonymized (EXIF-stripped, downscaled) before it
    reaches the collection store. The community delete-for-user path
    cascades through feedback rows AND the labeling candidates keyed
    by this user's scan_ids, so "delete my data" purges these too.
    """
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    today_writes = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: feedback_daily_writes(ip_hash),
    )
    if today_writes >= FEEDBACK_DAILY_QUOTA:
        raise HTTPException(
            status_code=429,
            detail="Daily feedback quota reached. Try again tomorrow.",
        )

    scan_id_clean = scan_id.strip()
    if not scan_id_clean or any(ch not in _SCAN_ID_ALLOWED for ch in scan_id_clean):
        raise HTTPException(status_code=422, detail="Invalid scan_id")
    issued = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: is_scan_id_issued(scan_id_clean, ttl_days=ISSUED_SCAN_ID_TTL_DAYS),
    )
    if not issued:
        raise HTTPException(
            status_code=422,
            detail="Unknown scan_id — please re-scan before sending feedback.",
        )

    # Hard size cap (same as /api/analyze) — the bare ``image.read()``
    # used here previously pulled the whole body into RAM with no ceiling.
    image_bytes = await _read_upload(image)
    saved = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: save_feedback_image(
            image_bytes,
            content_type=image.content_type,
        ),
    )
    if saved is None:
        raise HTTPException(
            status_code=422,
            detail="Image rejected (empty / oversized / write failed)",
        )
    filename, digest = saved

    updated = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: feedback_attach_image_path(scan_id_clean, filename),
    )

    # If the scan was never recorded (pre-recording install, recording
    # disabled, or a disputed not_a_plant the recorder skipped), build the
    # labeling candidate from the uploaded bytes via the SAME anonymize
    # helper — negative feedback must always reach the admin queue when we
    # have pixels for it. Fail-soft: the feedback upload already succeeded.
    def _enqueue_feedback_candidate() -> None:
        try:
            if labeling.candidate_for_scan(scan_id_clean) is not None:
                return  # mark_feedback already re-routed the recorded scan
            fb_row = feedback_latest_for_scan(scan_id_clean)
            cls = (
                (fb_row or {}).get("corrected_class")
                or (fb_row or {}).get("predicted_class")
            )
            if not cls or cls not in DISEASES_DATABASE:
                return  # nothing actionable to propose
            img = _open_image(image_bytes)
            body, sha, ph = _anonymise_scan_image(img)
            fn = f"{sha}.jpg"
            labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
            (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
            labeling.insert_candidate(
                cls=cls, image_path=fn, source="user_feedback", source_url=None,
                subject_plant=None, sha=sha, phash=ph, status="pending",
                model_pred=(fb_row or {}).get("predicted_class"),
                model_conf=(fb_row or {}).get("predicted_confidence"),
                scan_id=scan_id_clean,
                feedback_note=(fb_row or {}).get("note"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("feedback→labeling enqueue failed: %s", exc)

    asyncio.get_running_loop().run_in_executor(None, _enqueue_feedback_candidate)

    logger.info(
        "Feedback image stored scan=%s file=%s linked=%s",
        scan_id_clean[:16], filename, updated,
    )
    return JSONResponse(content={"ok": True, "filename": filename, "hash": digest})


# ── Community scan corpus ───────────────────────────────────────────


# Same magic-byte set the analyze endpoint accepts. We rely on
# Content-Type for the multipart form parse, then re-validate the
# bytes; an attacker can lie about Content-Type but the magic bytes
# don't lie.
_COMMUNITY_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


@api_router.post("/community/scan")
async def community_scan_upload(
    request: Request,
    scan_id: str = Form(...),
    user_pseudo_id: str = Form(...),
    country: str | None = Form(None),
    region: str = Form(""),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
    disease: str = Form(...),
    confidence: float | None = Form(None),
    species: str | None = Form(None),
    species_score: float | None = Form(None),
    locale: str | None = Form(None),
    nickname: str | None = Form(None),
    image: UploadFile | None = File(None),
):
    """Append one community-shared scan to the corpus.

    Location is supplied by the opted-in client from its coarse location
    (``country`` + ``region`` city + rounded ``lat``/``lon``) — there is no
    server-side IP geolocation. ``nickname`` is the author's generated handle.
    The image is optional.
    """

    try:
        payload = CommunityScanRequest(
            scan_id=scan_id,
            user_pseudo_id=user_pseudo_id,
            country=country,
            region=region,
            disease=disease,
            confidence=confidence,
            species=species,
            species_score=species_score,
            locale=locale,
            nickname=nickname,
        )
    except Exception as exc:  # noqa: BLE001 — pydantic raises a ValidationError subclass
        logger.info("Community scan validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid scan upload") from exc

    # Bind the shared scan to a scan_id we actually issued at /api/analyze —
    # the same integrity gate /api/feedback uses. Without it any client could
    # post a fabricated diagnosis (arbitrary species/disease/confidence) into
    # the public feed under a made-up or copied scan_id. On-device Aqua scans
    # have no server-issued id and are kept private client-side, so a
    # legitimate upload always carries an issued id. Checked BEFORE the quota
    # read and the (up to 10 MB) image body so a forged id is rejected without
    # us ingesting its payload.
    issued = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: is_scan_id_issued(payload.scan_id, ttl_days=ISSUED_SCAN_ID_TTL_DAYS),
    )
    if not issued:
        logger.warning(
            "Rejected community scan on unissued scan_id=%s",
            payload.scan_id[:16],
        )
        raise HTTPException(
            status_code=422,
            detail="Unknown scan_id — re-scan before sharing to the community.",
        )

    geo = _client_location(payload.country, payload.region, lat, lon)

    # Enforce the per-IP daily quota BEFORE reading the (up to 10 MB)
    # image body — an over-quota client should be rejected without us
    # ingesting its payload first.
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    today_writes = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: community_daily_writes(ip_hash),
    )
    if today_writes >= COMMUNITY_DAILY_QUOTA:
        logger.warning(
            "Community daily quota hit ip_hash=%s writes=%d cap=%d",
            ip_hash, today_writes, COMMUNITY_DAILY_QUOTA,
        )
        raise HTTPException(
            status_code=429,
            detail="Daily community-upload quota reached.",
        )

    image_bytes: bytes | None = None
    if image is not None:
        if image.content_type and image.content_type not in _COMMUNITY_ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail="Community thumbnail must be JPEG / PNG / WebP",
            )
        try:
            image_bytes = await _read_upload(image)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("Community thumb read failed: %s", exc)
            image_bytes = None
        # Re-validate the actual bytes — Content-Type is attacker-controlled
        # (and optional), but the magic bytes don't lie. Anything that isn't
        # a real JPEG/PNG/WebP is refused so arbitrary bytes can never be
        # stored and re-served from /api/community/thumb/.
        if image_bytes:
            head = image_bytes[:16]
            if not (
                head.startswith(b"\xff\xd8\xff")
                or head.startswith(b"\x89PNG\r\n\x1a\n")
                or _is_webp(head, image_bytes)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Community thumbnail must be JPEG / PNG / WebP",
                )
    try:
        row_id = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: community_record_scan(
                scan_id=payload.scan_id,
                user_pseudo_id=payload.user_pseudo_id,
                country=geo["country"] or "",
                region=geo["city"] or "",
                city=geo["city"],
                lat=geo["lat"],
                lon=geo["lon"],
                disease=payload.disease,
                confidence=payload.confidence,
                species=payload.species,
                nickname=payload.nickname,
                species_score=payload.species_score,
                image_bytes=image_bytes,
                locale=payload.locale,
                client_ip=client_ip,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Community scan write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to record scan") from exc

    # A new scan can change "popular near you" — drop the cached bundles so it
    # surfaces on the next request rather than only after the TTL lapses.
    _invalidate_popular_cache()
    return JSONResponse(content={"ok": True, "id": row_id})


class TelemetryErrorRequest(BaseModel):
    """One client-side problem report. Errors-only diagnostic context — no
    images, no precise location, no PII. ``breadcrumbs`` is a short trail of
    recent app actions leading up to the error."""

    stage: str = Field(..., min_length=1, max_length=64)
    kind: str = Field(..., min_length=1, max_length=64)
    fatal: bool = False
    http_status: int | None = Field(default=None, ge=0, le=599)
    family: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)
    android_api: int | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=2000)
    breadcrumbs: list[str] | None = Field(default=None, max_length=40)


@api_router.post("/telemetry/errors")
async def telemetry_errors(payload: TelemetryErrorRequest, request: Request):
    """Record one client error/problem for triage. Errors-only: the app sends
    nothing on the happy path and bundles diagnostic context (stage, kind,
    model family, HTTP status, app version, Android API, breadcrumbs) only when
    something goes wrong. Fire-and-forget from the client; degrades silently."""
    if not TELEMETRY_ENABLED:
        return JSONResponse(content={"ok": True, "recorded": False})
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    loop = asyncio.get_running_loop()
    today = await loop.run_in_executor(None, lambda: telemetry_daily_writes(ip_hash))
    if today >= TELEMETRY_DAILY_CAP_PER_IP:
        raise HTTPException(status_code=429, detail="Daily telemetry quota reached.")
    rid = await loop.run_in_executor(
        None,
        lambda: telemetry_record_error(
            stage=payload.stage,
            kind=payload.kind,
            fatal=payload.fatal,
            http_status=payload.http_status,
            family=payload.family,
            app_version=payload.app_version,
            android_api=payload.android_api,
            message=payload.message,
            breadcrumbs=payload.breadcrumbs,
            client_ip_hash=ip_hash,
        ),
    )
    return JSONResponse(content={"ok": True, "recorded": rid is not None})


class CommunityOpinionRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)
    user_pseudo_id: str = Field(..., min_length=1, max_length=128)
    suggested_disease: str = Field(..., min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    nickname: str | None = Field(default=None, max_length=64)

    @field_validator("nickname")
    @classmethod
    def _clean_opinion_nickname(cls, v: str | None) -> str | None:
        return _normalise_nickname(v)


@api_router.post("/community/opinion")
async def community_opinion_submit(payload: CommunityOpinionRequest, request: Request):
    """Record one community 'second opinion' on a shared scan.

    The suggested disease must be a known classifier class so the consensus
    tally stays clean; any free text lives only in the optional note.
    """
    disease = payload.suggested_disease.strip()
    if disease not in DISEASES_DATABASE:
        raise HTTPException(status_code=422, detail="Unknown suggested disease")

    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    loop = asyncio.get_running_loop()
    today = await loop.run_in_executor(
        None,
        lambda: community_daily_opinion_writes(ip_hash),
    )
    if today >= COMMUNITY_OPINION_DAILY_QUOTA:
        raise HTTPException(status_code=429, detail="Daily opinion quota reached.")

    # Viewers arrive with the feed's public token; the scan's owner (and
    # legacy cached feeds) with the real scan_id. Resolve to the real id
    # and require the shared scan to exist — opinions against ids that
    # were never shared are write-only noise in the consensus tally.
    sid = await loop.run_in_executor(
        None, lambda: community_resolve_scan_token(payload.scan_id.strip()),
    )
    known = await loop.run_in_executor(None, lambda: community_scan_exists(sid))
    if not known:
        raise HTTPException(status_code=422, detail="Unknown scan")
    # Public handles rotate every 30 days; a scan older than that goes
    # read-only so stale photos can't keep collecting opinions.
    fresh = await loop.run_in_executor(None, lambda: community_opinion_open(sid))
    if not fresh:
        raise HTTPException(status_code=410, detail="Scan too old for new opinions")
    try:
        await loop.run_in_executor(
            None,
            lambda: community_record_opinion(
                scan_id=sid,
                author_pseudo_id=payload.user_pseudo_id.strip(),
                suggested_disease=disease,
                note=payload.note,
                nickname=payload.nickname,
                client_ip=client_ip,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Community opinion write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to record opinion") from exc

    # Push the scan's owner a notification (best-effort; no-op when push isn't
    # configured). Skip self-opinions — you don't get pinged for your own note.
    # Only owners who explicitly REQUESTED a second opinion get pushed: sharing
    # a scan to the corpus is not a request to be notified.
    author = payload.user_pseudo_id.strip()

    def _push_owner() -> None:
        try:
            if not community_wants_opinion(sid):
                return
            owner = community_owner_of(sid)
            if owner and owner != author:
                push_send_opinion(owner, sid, disease, payload.note, payload.nickname)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Opinion push failed: %s", exc)

    await loop.run_in_executor(None, _push_owner)

    result = await loop.run_in_executor(
        None, lambda: community_opinions_for(sid),
    )
    return JSONResponse(
        content={"ok": True, "count": len(result["opinions"]), **result}
    )


class CommunityRequestOpinionRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)
    user_pseudo_id: str = Field(..., min_length=1, max_length=128)


@api_router.post("/community/request-opinion")
async def community_request_opinion(
    payload: CommunityRequestOpinionRequest, request: Request,
):
    """Flag the scanner's own shared scan as wanting a second opinion.

    Unlike ``/community/opinion`` (where a *viewer* suggests a diagnosis
    for someone else's scan), this is the *scanner* raising their hand:
    "I'm unsure — community, please weigh in." The flag is owner-scoped
    (the update only matches a row owned by ``user_pseudo_id``) and
    idempotent.

    Degrades gracefully: the community upload is fire-and-forget and the
    user may have opted out of sharing, so the owned row might not exist
    yet. We still return 200 with ``flagged: false`` — the client shows
    its local "waiting for the community" state regardless.
    """
    sid = payload.scan_id.strip()
    pid = payload.user_pseudo_id.strip()
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    loop = asyncio.get_running_loop()
    today = await loop.run_in_executor(
        None,
        lambda: community_daily_opinion_writes(ip_hash),
    )
    if today >= COMMUNITY_OPINION_DAILY_QUOTA:
        raise HTTPException(status_code=429, detail="Daily opinion quota reached.")
    try:
        flagged = await loop.run_in_executor(
            None,
            lambda: community_set_wants_opinion(sid, pid),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Community request-opinion failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to request opinion",
        ) from exc
    return JSONResponse(content={"ok": True, "flagged": bool(flagged)})


@api_router.post("/community/request-opinion/cancel")
async def community_request_opinion_cancel(payload: CommunityRequestOpinionRequest):
    """Withdraw an open second-opinion request on the caller's own scan.

    The inverse of ``/community/request-opinion``: drops the durable
    request row + the feed badge, so the community stops seeing the ask
    and the owner stops being notified about new opinions. Opinions
    already left stay visible. Owner-scoped and idempotent — cancelling
    a request that doesn't exist (or isn't yours) returns
    ``cleared: false`` with 200.

    (Requests also lapse on their own after
    ``OPINION_REQUEST_TTL_HOURS`` — see ``community.py``.)
    """
    sid = payload.scan_id.strip()
    pid = payload.user_pseudo_id.strip()
    loop = asyncio.get_running_loop()
    try:
        cleared = await loop.run_in_executor(
            None,
            lambda: community_clear_wants_opinion(sid, pid),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Community cancel-request-opinion failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to cancel opinion request",
        ) from exc
    return JSONResponse(content={"ok": True, "cleared": bool(cleared)})


@api_router.get("/community/opinions/{scan_id}")
async def community_opinions_list(scan_id: str):
    """List community opinions + a consensus tally for one shared scan.

    Accepts the real scan_id (owner's own app) or the feed's public
    token (viewers); echoes back whichever form the caller sent.
    """
    sid = (scan_id or "").strip()
    if not sid or len(sid) > 128:
        raise HTTPException(status_code=422, detail="Invalid scan id")
    loop = asyncio.get_running_loop()
    resolved = await loop.run_in_executor(
        None, lambda: community_resolve_scan_token(sid),
    )
    result = await loop.run_in_executor(
        None, lambda: community_opinions_for(resolved),
    )
    return JSONResponse(content={"scan_id": sid, **result})


@api_router.get("/community/my-scan-opinions/{user_pseudo_id}")
async def community_my_scan_opinions(user_pseudo_id: str):
    """Opinion counts on the caller's OWN shared scans. The client polls this
    on a schedule to notify the user when someone leaves a second opinion on a
    scan they shared (the per-scan view already exists via /opinions/{scan_id})."""
    uid = (user_pseudo_id or "").strip()
    if not uid or any(ch not in _SCAN_ID_ALLOWED for ch in uid):
        raise HTTPException(status_code=422, detail="Invalid user id")
    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: community_opinion_counts_for_user(uid),
    )
    return JSONResponse(content=result)


class PushRegisterRequest(BaseModel):
    user_pseudo_id: str = Field(..., min_length=1, max_length=128)
    token: str = Field(..., min_length=1, max_length=512)
    platform: str = Field(default="android", max_length=16)


@api_router.post("/push/register")
async def push_register_endpoint(payload: PushRegisterRequest):
    """Register this install's FCM token under its pseudo-id so the server can
    push opinion notifications to a closed / idle device. Upserts by token."""
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: push_register(
            payload.user_pseudo_id.strip(),
            payload.token.strip(),
            payload.platform.strip() or "android",
        ),
    )
    return JSONResponse(content={"ok": True})


class CommunityViewRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)
    user_pseudo_id: str = Field(..., min_length=1, max_length=128)


@api_router.post("/community/view")
async def community_record_scan_view(payload: CommunityViewRequest):
    """Record that the caller opened someone else's shared scan. Deduped per
    viewer server-side; a scan owner viewing their own scan and views of an
    unknown scan never count, so this is safe to fire on every open."""
    loop = asyncio.get_running_loop()
    # Viewers send the feed's public token — resolve it to the real id.
    sid = await loop.run_in_executor(
        None, lambda: community_resolve_scan_token(payload.scan_id.strip()),
    )
    counted = await loop.run_in_executor(
        None,
        lambda: community_record_view(sid, payload.user_pseudo_id.strip()),
    )
    return JSONResponse(content={"ok": True, "counted": bool(counted)})


class CommunityScanCountsRequest(BaseModel):
    scan_ids: list[str] = Field(..., max_length=500)


@api_router.post("/community/scan-counts")
async def community_scan_counts_endpoint(payload: CommunityScanCountsRequest):
    """Batched ``{opinions, views}`` per scan_id so the Gallery can badge the
    caller's own rows in one round-trip. Unknown/zero scans are omitted."""
    ids = [s.strip() for s in payload.scan_ids if s and s.strip()][:500]
    counts = (
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: community_scan_counts(ids),
        )
        if ids else {}
    )
    return JSONResponse(content={"counts": counts})


@api_router.get("/community/nearby")
async def community_scan_nearby(
    request: Request,
    country: str | None = None,
    region: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    disease: str | None = None,
    species: str | None = None,
    limit: int = 20,
    user_pseudo_id: str | None = None,
    scope: str | None = None,
):
    """List the most recent community-shared scans near the requester.

    Location is supplied by the opted-in client (coarse ``lat``/``lon`` +
    reverse-geocoded ``country``/``region`` city). ``scope`` is the app's
    filter toggle: ``nearby`` (default) restricts the feed to a ~30 km radius
    of the coords; ``country`` widens to the whole country. ``species`` +
    ``disease`` narrow the result-screen "similar scans" carousel, which
    widens nearby→country to stay filled.
    """
    geo = _client_location(country, region, lat, lon)
    country_norm = geo["country"]
    nearby = (scope or "nearby").strip().lower() != "country"
    has_coords = geo["lat"] is not None
    # With neither coords nor a country the query has no geo anchor, so the
    # feed comes back empty for a reason the client can't otherwise tell apart
    # from "no scans near you" (the usual cause: community opted in but the
    # coarse-location permission was never granted / no fix yet). Surface it as
    # an explicit flag so the client can prompt to enable location instead of
    # rendering a misleading empty state. Storage stays privacy-preserving —
    # there is deliberately no server-side IP geolocation.
    location_required = not (bool(country_norm) or has_coords)
    rows = community_nearby_scans(
        country=country_norm,
        lat=geo["lat"] if nearby else None,
        lon=geo["lon"] if nearby else None,
        disease=(disease or None),
        species=(species or None),
        limit=max(1, min(int(limit), 50)),
        exclude_user_pseudo_id=user_pseudo_id,
        scope=("nearby" if nearby else "country"),
    )
    # Country-level overview for the chip row (the coords feed itself is
    # radius-scoped; the summary stays country-wide for a stable "what's
    # around" readout). Excludes the requester's own rows so the row doesn't
    # surface the scan they just uploaded.
    summary = community_region_summary(
        country_norm or "",
        None,
        exclude_user_pseudo_id=user_pseudo_id,
    )
    # Annotate each cell with how many second opinions it has so the carousel
    # can show a badge, then swap the real scan_id for the public token —
    # the real id authorises /api/feedback writes and must never reach
    # other users (see community.public_scan_token).
    scan_ids = [str(r.get("scan_id")) for r in rows if r.get("scan_id")]
    counts = community_opinion_counts(scan_ids)
    views = community_view_counts(scan_ids)
    for r in rows:
        sid = str(r.get("scan_id"))
        r["opinion_count"] = counts.get(sid, 0)
        r["view_count"] = views.get(sid, 0)
        r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
        # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
        _localize_item_country(r)
    meta = _country_display(country_norm)
    # Tell the client which filter was actually served + the nearby label.
    meta["scope"] = "nearby" if (nearby and has_coords) else meta["scope"]
    meta["region"] = geo["city"] if (nearby and has_coords) else None
    meta["location_required"] = location_required
    summary.update(meta)
    return JSONResponse(content={"items": rows, "summary": summary, **meta})


def _valid_bbox(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
) -> tuple[float, float, float, float] | None:
    """Sanity-check + clamp a map bbox. Returns ``(lo_lat, lo_lon, hi_lat,
    hi_lon)`` within ±90/±180, or ``None`` when degenerate / out of range."""
    try:
        lo_lat, hi_lat = sorted((float(min_lat), float(max_lat)))
        lo_lon, hi_lon = sorted((float(min_lon), float(max_lon)))
    except (TypeError, ValueError):
        return None
    lo_lat, hi_lat = max(-90.0, lo_lat), min(90.0, hi_lat)
    lo_lon, hi_lon = max(-180.0, lo_lon), min(180.0, hi_lon)
    if hi_lat <= lo_lat or hi_lon <= lo_lon:
        return None
    return (lo_lat, lo_lon, hi_lat, hi_lon)


@api_router.get("/community/map")
async def community_map(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    disease: str | None = None,
    days: int = 30,
    user_pseudo_id: str | None = None,
    limit: int = 300,
):
    """Individual community scans inside a bbox — the zoomed-in map markers.

    Each scan carries its ~1 km-rounded coords + the PUBLIC token (never the
    real scan_id), so the app can pin a marker and open the existing opinion
    sheet. Public (no opt-in to view); ``not_a_plant`` excluded; ``truncated``
    signals an over-full bbox."""
    box = _valid_bbox(min_lat, min_lon, max_lat, max_lon)
    if box is None:
        raise HTTPException(status_code=422, detail="Invalid bbox")
    lo_lat, lo_lon, hi_lat, hi_lon = box
    data = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: community_map_scans(
            lo_lat, hi_lat, lo_lon, hi_lon,
            disease=(disease or None),
            days=days,
            limit=max(1, min(int(limit), 500)),
            exclude_user_pseudo_id=user_pseudo_id,
        ),
    )
    rows = data["items"]
    # Same opinion-count annotation + public-token swap + country localization as
    # the nearby feed; here we KEEP lat/lon for the markers.
    counts = community_opinion_counts([str(r.get("scan_id")) for r in rows if r.get("scan_id")])
    for r in rows:
        r["opinion_count"] = counts.get(str(r.get("scan_id")), 0)
        r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
        _localize_item_country(r)
    return JSONResponse(content={"items": rows, "truncated": data["truncated"]})


@api_router.get("/community/heatmap")
async def community_heatmap(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    disease: str | None = None,
    days: int = 30,
):
    """K-anonymized scan density (grid cells) for the zoomed-out heat layer.

    Public; returns only cell centers + counts (each ≥ 3 scans), never per-scan
    coords; ``not_a_plant`` excluded."""
    box = _valid_bbox(min_lat, min_lon, max_lat, max_lon)
    if box is None:
        raise HTTPException(status_code=422, detail="Invalid bbox")
    lo_lat, lo_lon, hi_lat, hi_lon = box
    data = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: community_density_grid(
            lo_lat, hi_lat, lo_lon, hi_lon,
            disease=(disease or None),
            days=days,
        ),
    )
    return JSONResponse(content=data)


@api_router.get("/community/my-opinions/{user_pseudo_id}")
async def community_my_opinions(user_pseudo_id: str):
    """List the second opinions one install has authored (newest first)."""
    pid = (user_pseudo_id or "").strip()
    if not pid or len(pid) > 128:
        raise HTTPException(status_code=422, detail="Invalid id")
    rows = await asyncio.get_running_loop().run_in_executor(
        None, lambda: community_opinions_by_author(pid),
    )
    return JSONResponse(content={"opinions": rows})


@api_router.get("/community/by-species")
async def community_by_species(
    species: str | None = None,
    limit: int = 12,
    user_pseudo_id: str | None = None,
):
    """Community scans of one species (by scientific name), across all
    regions — the result screen's 'other <species> in the community' strip."""
    sci = (species or "").strip()
    if not sci:
        raise HTTPException(status_code=422, detail="Invalid species")
    rows = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: community_scans_by_species(
            sci,
            limit=max(1, min(int(limit), 50)),
            exclude_user_pseudo_id=user_pseudo_id,
        ),
    )
    counts = community_opinion_counts([str(r.get("scan_id")) for r in rows if r.get("scan_id")])
    for r in rows:
        r["opinion_count"] = counts.get(str(r.get("scan_id")), 0)
        # Public token only — never the feedback-authorising real id.
        r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
        # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
        _localize_item_country(r)
    return JSONResponse(content={"items": rows})


@api_router.get("/species-info")
async def species_info_endpoint(name: str, locale: str = "en"):
    """Localized Wikipedia summary + lead image for a scientific name."""
    n = (name or "").strip()
    if not n or len(n) > 128:
        raise HTTPException(status_code=422, detail="Invalid species name")
    lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    info = await asyncio.get_running_loop().run_in_executor(
        None, lambda: fetch_species_info(n, lang),
    )
    if info is None:
        # Don't let an intermediary cache a transient Wikipedia miss.
        return JSONResponse(content={"available": False}, headers={"Cache-Control": "no-store"})
    # Route the Wikipedia photos through the server's cache so the app loads
    # them from our domain (reliable where Wikimedia is blocked). Unproxyable
    # entries drop to None/out so the card just omits them.
    proxied = {
        **info,
        "image_url": plant_photo_proxy(info.get("image_url")),
        "images": [p for p in (plant_photo_proxy(i) for i in info.get("images") or []) if p],
    }
    # Species facts are stable; carry a long Cache-Control so a CDN / HTTP cache
    # reuses it too (symmetric with /species-diseases). The app has its own
    # 7-day ResponseCache, so this mainly helps any intermediary layer.
    return JSONResponse(
        content={"available": True, **proxied},
        headers={"Cache-Control": "public, max-age=604800"},
    )


@api_router.get("/species-diseases")
async def species_diseases_endpoint(name: str, common: str = "", locale: str = "en"):
    """Common conditions a species is affected by indoors (AI, best-effort).

    Graceful ``{"available": False, "diseases": []}`` when no OpenRouter key
    is configured or on any failure — the client then renders its bundled
    disease guide, so the species screen's "common problems" section is never
    empty. A real (``available``) result is cached server-side (in-memory +
    on disk, surviving restarts) per (species, language) and carries a long
    ``Cache-Control`` so the client/CDN reuses it too; a transient failure is
    sent uncached so the next open retries.
    """
    n = (name or "").strip()
    if not n or len(n) > 128:
        raise HTTPException(status_code=422, detail="Invalid species name")
    lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    common_name = (common or "").strip()[:128] or None
    data = await asyncio.get_running_loop().run_in_executor(
        None, lambda: species_common_diseases(n, common_name=common_name, lang=lang),
    )
    headers = (
        {"Cache-Control": "public, max-age=604800"}
        if isinstance(data, dict) and data.get("available")
        else {"Cache-Control": "no-store"}
    )
    return JSONResponse(content=data, headers=headers)


def _cached_image_response(
    request: Request,
    path: Path,
    *,
    media_type: str,
    cache_control: str,
) -> StarletteResponse:
    """FileResponse with working HTTP revalidation.

    Starlette's ``FileResponse`` attaches an ETag but never *checks*
    ``If-None-Match``, so a revalidating client re-downloads the full
    bytes on every poll. This wrapper answers a matching conditional
    request with an empty 304 — for the guide photos and community
    thumbnails that is the difference between ~100 KB and ~100 bytes
    per revalidation on a mobile uplink.
    """
    st = path.stat()
    etag = f'W/"{int(st.st_mtime)}-{st.st_size}"'
    headers = {"Cache-Control": cache_control, "ETag": etag}
    inm = request.headers.get("if-none-match", "")
    if etag in {t.strip() for t in inm.split(",") if t.strip()}:
        return StarletteResponse(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


@api_router.get("/community/thumb/{filename}")
async def community_scan_thumb(filename: str, request: Request):
    """Serve a community thumbnail by filename.

    The corpus only ever stores filenames that ``community._save_thumbnail``
    minted, but we still hard-guard against path traversal: any
    filename containing ``/`` or ``..`` is refused outright. Missing
    files return 404 instead of 500.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid thumbnail name")
    path = COMMUNITY_THUMB_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    # Streams off the event loop; supports 304 revalidation. Thumbnails
    # are always re-encoded to JPEG by community._save_thumbnail.
    return _cached_image_response(
        request, path,
        media_type="image/jpeg",
        cache_control="public, max-age=86400",
    )


@api_router.get("/plant-photo")
async def plant_photo(request: Request, u: str | None = None,
                      name: str | None = None, locale: str = "en"):
    """Proxy + cache an external plant photo so the app loads it from this
    server's domain — reliable where Wikimedia is throttled/blocked.

    Two ways to address a photo:
    - ``u`` — a (URL-encoded) source URL (used for species-detail extra photos);
    - ``name`` — a scientific name the proxy *fully resolves* to its Wikipedia
      lead image server-side (used by the popular carousel, so a species that
      only just became popular still resolves a photo on demand).

    Only Wikimedia/Wikipedia hosts are ever fetched. 404 when the photo can't be
    cached (no image, fetch failure, or a non-raster format) — the client then
    shows its plant placeholder, the same graceful fallback as before."""
    loop = asyncio.get_running_loop()
    url = (u or "").strip()
    if url:
        if not plant_photo_allowed(url):
            raise HTTPException(status_code=400, detail="Invalid photo URL")
    elif name:
        nm = name.strip()[:128]
        lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
        info = await loop.run_in_executor(None, lambda: fetch_species_info(nm, lang))
        url = ((info or {}).get("image_url") or "").strip()
        if not url:
            raise HTTPException(status_code=404, detail="Photo unavailable")
    else:
        raise HTTPException(status_code=400, detail="Missing photo source")
    path = await loop.run_in_executor(None, lambda: plant_photo_get_or_fetch(url))
    if path is None:
        raise HTTPException(status_code=404, detail="Photo unavailable")
    # Wikimedia lead images come at full resolution (often multi-MB) — the home
    # carousel pulls ~10 of them, the worst payload on the main screen for a
    # slow connection. Downscale + WebP-transcode to a phone-sized variant once
    # and serve that; fall back to the original bytes if transcoding fails.
    # Backward compatible: Glide decodes WebP transparently on any app version.
    webp = await loop.run_in_executor(
        None, lambda: _webp_variant(path, _PLANT_PHOTO_MAX_SIDE, _PLANT_PHOTO_QUALITY),
    )
    serve, media = (
        (webp, "image/webp") if webp is not None
        else (path, plant_photo_media_type(path))
    )
    return _cached_image_response(
        request, serve,
        media_type=media,
        cache_control="public, max-age=604800",
    )


@api_router.get("/guide/manifest")
async def guide_manifest():
    """Keys that have an example photo, so the client can hide the photo strip
    for topics that ship none — preserving the old bundled-drawable behaviour
    (null ⇒ no strip) without probing each key with a 404."""
    keys = (
        sorted(p.stem for p in GUIDE_STATIC_DIR.glob("*.png"))
        if GUIDE_STATIC_DIR.is_dir() else []
    )
    return {"count": len(keys), "keys": keys}


# Large source images (guide PNGs, full-res Wikimedia plant photos) are
# downscaled + re-encoded to WebP once, lazily, into a sibling cache dir and
# served instead: far fewer bytes for a visually identical phone-sized image.
# The variant is keyed by the source file's SIZE (not mtime: a cache hit
# touches the original's mtime for the LRU, which would otherwise force a
# re-transcode on every request); a replaced source of a different size
# regenerates it. WebP is decoded transparently by Glide on any app vintage
# (it sniffs magic bytes, not the URL/extension), so this is backward
# compatible with already-shipped clients.
_WEBP_DIR_NAME = ".webp"
_webp_lock = threading.Lock()


def _webp_variant(src: Path, max_side: int, quality: int) -> Path | None:
    """Return a cached downscaled-WebP variant of ``src`` (``max_side`` px on
    the longest edge, ``quality`` 0–100), generating it if stale.

    Any failure (PIL hiccup, unwritable dir) returns ``None`` and the caller
    falls back to serving the original — the optimisation must never take the
    endpoint down.
    """
    try:
        out_dir = src.parent / _WEBP_DIR_NAME
        # Key the variant on the source SIZE, not its mtime. plant_photos
        # touches a cached original's mtime on every hit (its LRU signal), so an
        # mtime-based freshness check would re-transcode on EVERY request and
        # churn the served file's ETag. Size is stable across access-touch; a
        # genuinely replaced source (different bytes ⇒ different size) still gets
        # a fresh variant under a new name.
        out = out_dir / f"{src.stem}.{src.stat().st_size}.webp"
        if out.is_file():
            return out
        # One transcode at a time — two concurrent first requests for the
        # same key must not interleave writes into the same target file.
        with _webp_lock:
            if out.is_file():
                return out
            out_dir.mkdir(parents=True, exist_ok=True)
            img = Image.open(src)
            img.load()
            img = img.convert("RGB")
            w, h = img.size
            scale = max_side / float(max(w, h))
            if scale < 1.0:
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS,
                )
            tmp = out.with_suffix(".webp.tmp")
            img.save(tmp, format="WEBP", quality=quality, method=4)
            tmp.replace(out)
        return out
    except Exception as exc:  # noqa: BLE001 — fall back to the original
        logger.warning("WebP transcode failed for %s: %s", src, exc)
        return None


# Guide photos ship as large PNGs (80–565 KB of photographic content — the
# worst case for PNG); 800 px is plenty for the in-app example strips.
_GUIDE_WEBP_MAX_SIDE = 800
_GUIDE_WEBP_QUALITY = 80


def _guide_webp_variant(src: Path) -> Path | None:
    return _webp_variant(src, _GUIDE_WEBP_MAX_SIDE, _GUIDE_WEBP_QUALITY)


# Plant-photo proxy: this one URL feeds both the small home carousel and the
# species-detail hero, so 1000 px keeps the hero crisp while still cutting a
# multi-MB Wikimedia original to tens of KB. Config-driven so a deploy can tune
# the bandwidth/quality trade-off without a code change.
_PLANT_PHOTO_MAX_SIDE = get_int("PLANT_PHOTO_MAX_SIDE", 1000)
_PLANT_PHOTO_QUALITY = get_int("PLANT_PHOTO_QUALITY", 80)


@api_router.get("/guide/photo/{key}")
async def guide_photo(key: str, request: Request):
    """Serve a guide example photo by key (e.g. ``powdery_mildew``).

    Path-traversal guarded like the community-thumb route; missing keys 404.
    Long-cached because the photos are immutable per release. Serves the
    downscaled WebP variant when available (Glide sniffs the magic bytes,
    so clients of any vintage decode it transparently); the original PNG
    is the fallback if transcoding ever fails.
    """
    if "/" in key or "\\" in key or ".." in key:
        raise HTTPException(status_code=400, detail="Invalid guide photo key")
    path = GUIDE_STATIC_DIR / f"{key}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Guide photo not found")
    webp = await asyncio.get_running_loop().run_in_executor(
        None, _guide_webp_variant, path,
    )
    serve, media = (webp, "image/webp") if webp is not None else (path, "image/png")
    return _cached_image_response(
        request, serve,
        media_type=media,
        cache_control="public, max-age=604800",
    )


# Cache the model digest by (path, mtime, size) so we hash the (multi-MB)
# tflite once per provisioned file instead of on every manifest poll.
# lru_cache is thread-safe and bounded (the previous hand-rolled dict had no
# lock — concurrent manifest polls could interleave on it).
@functools.lru_cache(maxsize=4)
def _aqua_model_sha256(path: Path, mtime: float, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@api_router.get("/aqua/manifest")
async def aqua_manifest():
    """Describe the hosted Aqua on-device model so the app can decide whether
    to (re)download it. ``available:false`` when the artefact isn't provisioned
    — the app then just uses the server model. ``sha256`` lets the client skip
    a re-download when it already has the current bytes; ``classes`` is the
    sidecar metadata (class order, input size, normalisation) the app needs to
    run the model."""
    model_path = AQUA_STATIC_DIR / "aqua_classifier.tflite"
    meta_path = AQUA_STATIC_DIR / "classes.json"
    if not model_path.is_file() or not meta_path.is_file():
        return {"available": False}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    st = model_path.stat()
    return {
        "available": True,
        "version": meta.get("model_version", "unknown"),
        "sha256": _aqua_model_sha256(model_path, st.st_mtime, st.st_size),
        "size_bytes": st.st_size,
        "classes": meta,
    }


@api_router.get("/aqua/model")
async def aqua_model():
    """Serve the Aqua TFLite model bytes. 404 when not provisioned."""
    model_path = AQUA_STATIC_DIR / "aqua_classifier.tflite"
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail="Aqua model not provisioned")
    return FileResponse(
        model_path,
        media_type="application/octet-stream",
        filename="aqua_classifier.tflite",
        headers={"Cache-Control": "no-cache"},
    )


@api_router.delete("/community/user/{user_pseudo_id}")
async def community_scan_delete_user(user_pseudo_id: str):
    """Erase all rows + thumbnails attributed to one install. Used by
    the Android "delete my contributions" affordance.
    """
    if not user_pseudo_id or any(ch not in _SCAN_ID_ALLOWED for ch in user_pseudo_id):
        raise HTTPException(status_code=422, detail="Invalid user id")
    loop = asyncio.get_running_loop()
    # Resolve the user's scan_ids BEFORE deleting the feedback rows — the
    # feedback DB holds the only user↔scan_id join, and the labeling
    # candidates (recorded scans / feedback uploads) are keyed by scan_id.
    scan_ids = await loop.run_in_executor(
        None,
        lambda: feedback_scan_ids_for_user(user_pseudo_id),
    )
    deleted_community = await loop.run_in_executor(
        None,
        lambda: community_delete_for_user(user_pseudo_id),
    )
    deleted_feedback = await loop.run_in_executor(
        None,
        lambda: feedback_delete_for_user(user_pseudo_id),
    )
    deleted_labeling = await loop.run_in_executor(
        None,
        lambda: labeling.delete_candidates_for_scan_ids(scan_ids),
    )
    return JSONResponse(content={
        "ok": True,
        "deleted": deleted_community,
        "deleted_community": deleted_community,
        "deleted_feedback": deleted_feedback,
        "deleted_labeling_candidates": deleted_labeling,
    })


@api_router.get("/user/export/{user_pseudo_id}")
async def export_user_data(user_pseudo_id: str):
    """GDPR Data Portability: Export all data associated with the user_pseudo_id."""
    if not user_pseudo_id or any(ch not in _SCAN_ID_ALLOWED for ch in user_pseudo_id):
        raise HTTPException(status_code=422, detail="Invalid user id")
    community_data = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: community_get_user_data(user_pseudo_id),
    )
    feedback_data = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: feedback_get_user_data(user_pseudo_id),
    )
    return JSONResponse(content={
        "user_pseudo_id": user_pseudo_id,
        "community_scans": community_data,
        "feedback_submissions": feedback_data,
    })


# --- /api/popular: personalized, short-cached popular-houseplants bundle ----
# key "{country|'global'}:{locale}" -> (expiry_epoch, payload). The list is a
# live community-scan aggregate, so the bundle is cached only briefly (and
# served stale-while-revalidate) — new scans surface within the TTL instead of
# being pinned for hours. The (static) per-species photos are NOT re-resolved
# on a rebuild: they're served by the by-name /api/plant-photo proxy, which has
# its own long-lived disk cache. So a short bundle TTL is cheap.
_POPULAR_TTL_S = get_int("POPULAR_BUNDLE_TTL_SECONDS", 1800)
_popular_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_popular_lock = threading.Lock()
_POPULAR_CACHE_MAX = 64


# Both home carousels (popular plants + popular diseases) cap at 10 cards.
_POPULAR_MAX_CARDS = 10

# A placeholder (scan-less country) is cached only briefly so it flips to real
# data within minutes of the first scan in that scope; real data keeps the
# full TTL since aggregates move slowly.
_POPULAR_PLACEHOLDER_TTL_S = get_int("POPULAR_PLACEHOLDER_TTL_SECONDS", 300)


def _popular_ttl(bundle: dict) -> int:
    return (_POPULAR_PLACEHOLDER_TTL_S
            if bundle.get("source") == "placeholder" else _POPULAR_TTL_S)


def _invalidate_popular_cache() -> None:
    """Drop the cached popular bundles so a freshly uploaded community scan
    surfaces in "popular near you" on the next request instead of waiting out
    the TTL. Bundles are cheap to rebuild (a DB aggregate over the geo index +
    already-cached per-species titles/photos), so clearing all of them — rather
    than trying to map one upload's coords to the affected region keys — is the
    simple, correct choice. Keeps the carousel near-live wrt new scans."""
    with _popular_lock:
        _popular_cache.clear()


async def _warm_placeholder_images() -> None:
    """Warm the cold path so the first home request is instant: pre-resolve the
    curated placeholder plants' lead images, then prebuild + cache the
    worldwide bundle (the not-opted-in default) for both locales. Best-effort."""
    loop = asyncio.get_running_loop()
    seen: set[str] = set()
    warmed_photos: set[str] = set()
    for p in popular_for(None):
        if p.scientific in seen:
            continue
        seen.add(p.scientific)
        for lang in ("en", "ru"):
            info = None
            with contextlib.suppress(Exception):
                info = await loop.run_in_executor(
                    None, lambda s=p.scientific, n=lang: fetch_species_info(s, n),
                )
            # Pre-cache the lead photo bytes too, so the very first carousel
            # request serves images instantly from our disk cache.
            url = (info or {}).get("image_url")
            if url and url not in warmed_photos:
                warmed_photos.add(url)
                with contextlib.suppress(Exception):
                    await loop.run_in_executor(None, lambda u=url: plant_photo_get_or_fetch(u))
    for locale in ("en", "ru"):
        with contextlib.suppress(Exception):
            bundle = await loop.run_in_executor(
                None, lambda n=locale: _build_popular_bundle(None, None, None, None, n),
            )
            key = f"||{locale}"
            with _popular_lock:
                _popular_cache[key] = (time.time() + _popular_ttl(bundle), bundle)
                _popular_cache.move_to_end(key)


def _build_popular_bundle(
    country: str | None,
    city: str | None,
    lat: float | None,
    lon: float | None,
    locale: str,
) -> dict:
    """Popular species + diseases for the home carousels, with the ladder
    nearby(≤30 km) → country → worldwide → curated placeholder.

    Real-scan scopes carry per-item ``count``/``percent`` (the client shows
    "P% of scans"); the placeholder carries zeros + ``source="placeholder"``
    so the client suppresses the percentage. Both lists are capped at 10.
    """
    lang = "ru" if locale == "ru" else "en"

    def _has(snap: dict) -> bool:
        return bool(snap.get("top_species") or snap.get("top_diseases"))

    snap: dict | None = None
    source = "worldwide"
    region_label: str | None = None
    if lat is not None and lon is not None:
        s = community_popular_snapshot(lat=lat, lon=lon, limit=_POPULAR_MAX_CARDS)
        if _has(s):
            snap, source, region_label = s, "nearby", city
    if snap is None and country:
        s = community_popular_snapshot(country=country, limit=_POPULAR_MAX_CARDS)
        if _has(s):
            snap, source = s, "country"
    if snap is None:
        s = community_popular_snapshot(limit=_POPULAR_MAX_CARDS)  # all regions
        if _has(s):
            snap, source = s, "worldwide"

    # No community data in any scope → curated placeholder. Titled for the
    # known country when we have one, else worldwide. The first real scan in
    # range flips this to live data on the next (short-lived) cache miss.
    if snap is None:
        # Curated names are bundled, so the placeholder build does NO network:
        # the photo is resolved lazily + cached by the by-name proxy.
        items = [
            {
                "scientific": p.scientific,
                "common_en": p.common_en,
                "common_ru": p.common_ru,
                "image_url": plant_photo_name_proxy(p.scientific, lang),
                "count": 0,
                "percent": 0,
            }
            for p in popular_for(country)[:_POPULAR_MAX_CARDS]
        ]
        return {
            **_country_display(country),
            "locale": locale,
            "region": None,
            "items": items,
            "top_diseases": [],
            "total": 0,
            "source": "placeholder",
        }

    display = _country_display(country) if source != "worldwide" else _country_display(None)
    items = []
    for row in (snap.get("top_species") or [])[:_POPULAR_MAX_CARDS]:
        scientific = row["species"]
        # Only the localized title needs resolving here (cached); the photo is
        # resolved + cached on demand by the by-name proxy, so a species that
        # only just trended (a fresh scan) still gets an image.
        info = fetch_species_info(scientific, lang)
        title = (info or {}).get("title") or scientific
        items.append({
            "scientific": scientific,
            "common_en": title,
            "common_ru": title,
            "image_url": plant_photo_name_proxy(scientific, lang),
            "count": row.get("count", 0),
            "percent": row.get("percent", 0),
        })
    return {
        **display,
        "locale": locale,
        "region": region_label if source == "nearby" else None,
        "items": items,
        "top_diseases": (snap.get("top_diseases") or [])[:_POPULAR_MAX_CARDS],
        "total": snap.get("total", 0),
        "source": source,
    }


@api_router.get("/popular")
async def popular_endpoint(request: Request, country: str | None = None,
                           region: str | None = None, lat: float | None = None,
                           lon: float | None = None, locale: str = "en"):
    """Personalized popular-houseplants bundle with lead images pre-resolved.

    Location is supplied by the opted-in client (coarse ``lat``/``lon`` +
    reverse-geocoded ``country``/``region`` city); a request with none gets the
    worldwide bundle. Cached per (country, coords, locale); a stale entry is
    served immediately and refreshed in the background so a user never waits on
    the per-species Wikipedia calls."""
    geo = _client_location(country, region, lat, lon)
    country_norm = geo["country"]
    lat_n, lon_n = geo["lat"], geo["lon"]
    city_n = geo["city"]
    locale = "ru" if str(locale or "en").lower().startswith("ru") else "en"

    key = f"{country_norm or ''}|{lat_n or ''},{lon_n or ''}|{locale}"
    now = time.time()
    with _popular_lock:
        hit = _popular_cache.get(key)
        if hit:
            _popular_cache.move_to_end(key)
    bundle = hit[1] if hit else None
    fresh = bool(hit and hit[0] > now)

    if bundle is None:
        # Cold: build synchronously (first ever request for this scope).
        bundle = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _build_popular_bundle(country_norm, city_n, lat_n, lon_n, locale),
        )
        with _popular_lock:
            _popular_cache[key] = (now + _popular_ttl(bundle), bundle)
            _popular_cache.move_to_end(key)
            while len(_popular_cache) > _POPULAR_CACHE_MAX:
                _popular_cache.popitem(last=False)
    elif not fresh:
        # Stale: serve it now, refresh in the background (deduped per key).
        _refresh_popular_bg(key, country_norm, city_n, lat_n, lon_n, locale)

    return JSONResponse(
        content=bundle,
        headers={"cache-control": f"public, max-age={_popular_ttl(bundle)}"},
    )


# Keys currently being refreshed in the background, so concurrent stale hits
# don't each spawn a rebuild.
_popular_refreshing: set[str] = set()


def _refresh_popular_bg(
    key: str, country: str | None, city: str | None,
    lat, lon, locale: str,
) -> None:
    with _popular_lock:
        if key in _popular_refreshing:
            return
        _popular_refreshing.add(key)

    async def _run() -> None:
        try:
            bundle = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _build_popular_bundle(country, city, lat, lon, locale),
            )
            with _popular_lock:
                _popular_cache[key] = (time.time() + _popular_ttl(bundle), bundle)
                _popular_cache.move_to_end(key)
                while len(_popular_cache) > _POPULAR_CACHE_MAX:
                    _popular_cache.popitem(last=False)
        except Exception as exc:  # noqa: BLE001 — background best-effort
            logger.info("popular background refresh failed: %s", exc)
        finally:
            with _popular_lock:
                _popular_refreshing.discard(key)

    with contextlib.suppress(RuntimeError):  # no running loop (shouldn't happen)
        asyncio.create_task(_run())


app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=get_str("HOST", "0.0.0.0"),
        port=get_int("PORT", 8000),
        forwarded_allow_ips=get_str("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
