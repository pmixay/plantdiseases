"""
PlantDiseases Server — FastAPI backend with the PlantVision two-stage
plant disease pipeline (houseplants + outdoor/garden ornamentals).

Stage 1:  YOLOv8n              — locate plant leaves (single class)
Stage 2:  DINOv2 ViT-S/14      — frozen backbone + LoRA + MLP head
                                 (the ``gamma`` family), classifies
                                 cropped leaf regions into 9 classes
                                 incl. a ``not_a_plant`` head; mean-voted
                                 across bboxes, served via ONNX.

Model families (per-bundle ``classes.json -> model_family``): ``aqua``
(MobileNetV3-Small, distilled → TFLite float32, on-device), ``gamma``
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
import sys
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

# Imports work two ways:
#   1) Flat, from `server/` (`uvicorn main:app`, which is what `start.sh`
#      runs) — `from ai_services import …` resolves directly.
#   2) From the repo root (`python -m server.main` or `uvicorn
#      server.main:app`) — same names live under the `server.` package.
# The try/except below covers both without forcing PYTHONPATH gymnastics.
try:
    from . import (
        account_roles,
        community_achievements,
        community_forum,
        community_labels,
        community_moderation,
        routes_assistant,
        routes_community,
        routes_content,
        routes_diagnosis,
        routes_forum,
        routes_internal,
        routes_meta,
        routes_moderation,
        training_store,
    )
    from .account_auth import (
        AccountAuthenticationError,
        AccountAuthenticationUnavailable,
        AccountPrincipal,
        build_account_authenticator,
    )
    from .account_entitlements import (
        EntitlementDecision,
        EntitlementUnavailable,
        account_entitlement_client,
    )
    from .account_internal import (
        internal_service_authorized,
    )
    from .account_internal import (
        send_account_opinion_notification as _send_account_opinion_notification,
    )
    from .ai_services import (  # type: ignore[import-not-found]
        care_plan_metrics_snapshot,
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
        openrouter_configured,
        plantnet_cache_lookup,
        plantnet_configured,
        plantnet_metrics_snapshot,
        prompt_cache_metrics_snapshot,
        species_common_diseases,
        species_profile,
    )
    from .beta_runtime import allow_synthetic_pipeline
    from .community import (
        COMMUNITY_THUMB_DIR,
        AnalysisImageMismatchError,
        AnalysisTokenConflictError,
        AnalysisTokenUnknownError,
        OpinionTargetUnavailableError,
    )
    from .community import (
        account_owner_of as community_account_owner_of,
    )
    from .community import (
        actor_blocks as community_actor_blocks,
    )
    from .community import (
        anonymous_popular_snapshot as community_anonymous_popular_snapshot,
    )
    from .community import (
        block_content_author as community_block_content_author,
    )
    from .community import (
        clear_wants_opinion as community_clear_wants_opinion,
    )
    from .community import (
        decode_recommendation_cursor as community_decode_recommendation_cursor,
    )
    from .community import (
        density_grid as community_density_grid,
    )
    from .community import (
        encode_recommendation_cursor as community_encode_recommendation_cursor,
    )
    from .community import (
        init_db as init_community_db,
    )
    from .community import (
        is_analysis_token_issued as community_analysis_token_issued,
    )
    from .community import (
        like_counts as community_like_counts,
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
        opinion_counts_for_actor as community_opinion_counts_for_actor,
    )
    from .community import (
        opinion_open as community_opinion_open,
    )
    from .community import (
        opinions_for as community_opinions_for,
    )
    from .community import (
        popular_snapshot as community_popular_snapshot,
    )
    from .community import (
        process_cleanup_jobs as community_process_cleanup_jobs,
    )
    from .community import (
        prune_older_than as community_prune_older_than,
    )
    from .community import (
        public_account_profile as community_public_account_profile,
    )
    from .community import (
        recommended_scans as community_recommended_scans,
    )
    from .community import (
        record_analysis_scan as community_record_analysis_scan,
    )
    from .community import (
        record_analysis_token as community_record_analysis_token,
    )
    from .community import (
        record_anonymous_scan as community_record_anonymous_scan,
    )
    from .community import (
        record_content_report as community_record_content_report,
    )
    from .community import (
        record_opinion_with_status as community_record_opinion,
    )
    from .community import (
        record_views as community_record_views,
    )
    from .community import (
        region_summary as community_region_summary,
    )
    from .community import (
        request_actor_deletion as community_request_actor_deletion,
    )
    from .community import (
        resolve_scan_token as community_resolve_scan_token,
    )
    from .community import (
        scan_counts_for_actor as community_scan_counts_for_actor,
    )
    from .community import (
        scan_exists as community_scan_exists,
    )
    from .community import (
        scans_by_species as community_scans_by_species,
    )
    from .community import (
        set_like as community_set_like,
    )
    from .community import (
        set_wants_opinion as community_set_wants_opinion,
    )
    from .community import (
        stats_snapshot as community_stats_snapshot,
    )
    from .community import (
        thumbnail_is_visible as community_thumbnail_is_visible,
    )
    from .community import (
        unblock_actor as community_unblock_actor,
    )
    from .community import (
        update_actor_nickname as community_update_actor_nickname,
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
    from .pipeline import (
        ABSTENTION_REASON_NAP as _ABSTENTION_REASON_NAP,
    )
    from .pipeline import (
        ABSTENTION_REASON_UNKNOWN as _ABSTENTION_REASON_UNKNOWN,
    )
    from .pipeline import (
        PlantDiseasePipeline,
        _derive_abstention,
    )
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
    from .plant_photos import prune_cache as plant_photo_prune_cache
    from .plant_photos import warm as plant_photo_warm
    from .popular_plants import popular_for
    from .runtime_paths import normalize_root_path
    from .species_info import fetch_species_info
    from .symptoms import diagnose as symptom_diagnose
    from .synthetic_pipeline import SyntheticPipeline
    from .telemetry import (
        ALLOWED_BREADCRUMBS as TELEMETRY_ALLOWED_BREADCRUMBS,
    )
    from .telemetry import (
        ALLOWED_FAMILIES as TELEMETRY_ALLOWED_FAMILIES,
    )
    from .telemetry import (
        ALLOWED_KINDS as TELEMETRY_ALLOWED_KINDS,
    )
    from .telemetry import (
        ALLOWED_STAGES as TELEMETRY_ALLOWED_STAGES,
    )
    from .telemetry import (
        android_api_bucket as telemetry_android_api_bucket,
    )
    from .telemetry import (
        http_status_bucket as telemetry_http_status_bucket,
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
    import account_roles
    import community_achievements
    import community_forum
    import community_labels
    import community_moderation
    import routes_assistant
    import routes_community
    import routes_content
    import routes_diagnosis
    import routes_forum
    import routes_internal
    import routes_meta
    import routes_moderation
    import training_store
    from account_auth import (
        AccountAuthenticationError,
        AccountAuthenticationUnavailable,
        AccountPrincipal,
        build_account_authenticator,
    )
    from account_entitlements import (
        EntitlementDecision,
        EntitlementUnavailable,
        account_entitlement_client,
    )
    from account_internal import (
        internal_service_authorized,
    )
    from account_internal import (
        send_account_opinion_notification as _send_account_opinion_notification,
    )
    from ai_services import (
        care_plan_metrics_snapshot,
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
        openrouter_configured,
        plantnet_cache_lookup,
        plantnet_configured,
        plantnet_metrics_snapshot,
        prompt_cache_metrics_snapshot,
        species_common_diseases,
        species_profile,
    )
    from beta_runtime import allow_synthetic_pipeline
    from community import (
        COMMUNITY_THUMB_DIR,
        AnalysisImageMismatchError,
        AnalysisTokenConflictError,
        AnalysisTokenUnknownError,
        OpinionTargetUnavailableError,
    )
    from community import (
        account_owner_of as community_account_owner_of,
    )
    from community import (
        actor_blocks as community_actor_blocks,
    )
    from community import (
        anonymous_popular_snapshot as community_anonymous_popular_snapshot,
    )
    from community import (
        block_content_author as community_block_content_author,
    )
    from community import (
        clear_wants_opinion as community_clear_wants_opinion,
    )
    from community import (
        decode_recommendation_cursor as community_decode_recommendation_cursor,
    )
    from community import (
        density_grid as community_density_grid,
    )
    from community import (
        encode_recommendation_cursor as community_encode_recommendation_cursor,
    )
    from community import (
        init_db as init_community_db,
    )
    from community import (
        is_analysis_token_issued as community_analysis_token_issued,
    )
    from community import (
        like_counts as community_like_counts,
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
        opinion_counts_for_actor as community_opinion_counts_for_actor,
    )
    from community import (
        opinion_open as community_opinion_open,
    )
    from community import (
        opinions_for as community_opinions_for,
    )
    from community import (
        popular_snapshot as community_popular_snapshot,
    )
    from community import (
        process_cleanup_jobs as community_process_cleanup_jobs,
    )
    from community import (
        prune_older_than as community_prune_older_than,
    )
    from community import (
        public_account_profile as community_public_account_profile,
    )
    from community import (
        recommended_scans as community_recommended_scans,
    )
    from community import (
        record_analysis_scan as community_record_analysis_scan,
    )
    from community import (
        record_analysis_token as community_record_analysis_token,
    )
    from community import (
        record_anonymous_scan as community_record_anonymous_scan,
    )
    from community import (
        record_content_report as community_record_content_report,
    )
    from community import (
        record_opinion_with_status as community_record_opinion,
    )
    from community import (
        record_views as community_record_views,
    )
    from community import (
        region_summary as community_region_summary,
    )
    from community import (
        request_actor_deletion as community_request_actor_deletion,
    )
    from community import (
        resolve_scan_token as community_resolve_scan_token,
    )
    from community import (
        scan_counts_for_actor as community_scan_counts_for_actor,
    )
    from community import (
        scan_exists as community_scan_exists,
    )
    from community import (
        scans_by_species as community_scans_by_species,
    )
    from community import (
        set_like as community_set_like,
    )
    from community import (
        set_wants_opinion as community_set_wants_opinion,
    )
    from community import (
        stats_snapshot as community_stats_snapshot,
    )
    from community import (
        thumbnail_is_visible as community_thumbnail_is_visible,
    )
    from community import (
        unblock_actor as community_unblock_actor,
    )
    from community import (
        update_actor_nickname as community_update_actor_nickname,
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
    from pipeline import (
        ABSTENTION_REASON_NAP as _ABSTENTION_REASON_NAP,
    )
    from pipeline import (
        ABSTENTION_REASON_UNKNOWN as _ABSTENTION_REASON_UNKNOWN,
    )
    from pipeline import (
        PlantDiseasePipeline,
        _derive_abstention,
    )
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
    from plant_photos import prune_cache as plant_photo_prune_cache
    from plant_photos import warm as plant_photo_warm
    from popular_plants import popular_for
    from runtime_paths import normalize_root_path
    from species_info import fetch_species_info
    from symptoms import diagnose as symptom_diagnose
    from synthetic_pipeline import SyntheticPipeline
    from telemetry import (
        ALLOWED_BREADCRUMBS as TELEMETRY_ALLOWED_BREADCRUMBS,
    )
    from telemetry import (
        ALLOWED_FAMILIES as TELEMETRY_ALLOWED_FAMILIES,
    )
    from telemetry import (
        ALLOWED_KINDS as TELEMETRY_ALLOWED_KINDS,
    )
    from telemetry import (
        ALLOWED_STAGES as TELEMETRY_ALLOWED_STAGES,
    )
    from telemetry import (
        android_api_bucket as telemetry_android_api_bucket,
    )
    from telemetry import (
        http_status_bucket as telemetry_http_status_bucket,
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
    ``/api/capabilities``. Prefer the package ``__version__``; when the server runs
    as a flat module (``uvicorn main:app`` from ``server/``) that import fails, so
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
            # Exception strings can embed request URLs, file paths, tokens, or
            # provider response bodies. Keep only the fact that one existed.
            log_entry["exception"] = "redacted"
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
ENVIRONMENT = get_str("ENVIRONMENT", "").strip().lower()
ACCOUNT_BETA_SYNTHETIC_PIPELINE = allow_synthetic_pipeline(
    environment=ENVIRONMENT,
    requested=get_bool("ACCOUNT_BETA_SYNTHETIC_PIPELINE", False),
    synthetic_staging_acknowledged=get_bool(
        "ACCOUNT_BETA_SYNTHETIC_DATA_ONLY",
        False,
    ),
)
APP_ROOT_PATH = normalize_root_path(get_str("APP_ROOT_PATH", ""))
POPULAR_WARM_ENABLED = get_bool("POPULAR_WARM_ENABLED", True)
ACCOUNT_FEATURE_EXPECTED = get_bool(
    "ACCOUNT_FEATURE_EXPECTED",
    bool(
        get_str("ACCOUNT_API_BASE_URL", "").strip()
        or get_str("ACCOUNT_ISSUER", "").strip()
    ),
)
account_authenticator = build_account_authenticator(expected=ACCOUNT_FEATURE_EXPECTED)
community_bearer = HTTPBearer(auto_error=False)

MODELS_DIR = Path(get_str("MODELS_DIR", "models"))
# These anchors identify the bundle directory only. Strict verification reads
# bundle_manifest.json and returns the exact checksummed ONNX paths; there is no
# filename preference or graph fallback.
DETECTOR_PATH = MODELS_DIR / "detector.onnx"
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
# Frontier lives in its own complete manifest-bound directory so no detector,
# classifier, taxonomy, or calibration file can collide with gamma's bundle.
FRONTIER_MODELS_DIR = Path(get_str("FRONTIER_MODELS_DIR", str(MODELS_DIR / "frontier")))
FRONTIER_DETECTOR_PATH = FRONTIER_MODELS_DIR / "detector.onnx"
FRONTIER_CLASSIFIER_PATH = FRONTIER_MODELS_DIR / "classifier.onnx"

# ── De-identified training-capture trust knobs ───────────────────────
# Confident weak labels become ``auto_yes``; uncertainty remains ``pending``
# for offline-only review. No serving-side human/admin queue exists.
TRAINING_TRUST_CONFORMAL = get_bool("TRAINING_TRUST_CONFORMAL", True)
TRAINING_CONF_HIGH = get_float("TRAINING_CONF_HIGH", 0.85)
# Value-driven scan retention: every disease class is high training signal and
# is always kept; the abundant easy class (healthy; not_a_plant is dropped
# upstream) is sampled at TRAINING_HEALTHY_KEEP_RATE so confident-healthy photos
# can't drown the disease corpus.
DISEASE_CLASSES = {
    "gray_mold_botrytis", "leaf_spot", "leaf_scorch_underwatering",
    "nutrient_deficiency", "pests", "powdery_mildew", "viral_disease",
}

# ── Consent-gated training capture ───────────────────────────────────
# The operator switch is necessary but never sufficient: each analyze request
# must separately present the current disclosure version. Automatic capture
# stores only a detector-backed leaf crop, never a full room/background frame.
TRAINING_CAPTURE_ENABLED = get_bool("TRAINING_CAPTURE_ENABLED", False)
TRAINING_CONSENT_VERSION = get_str(
    "TRAINING_CONSENT_VERSION", "model-improvement-v1"
).strip()
TRAINING_RETENTION_DAYS = get_int("TRAINING_RETENTION_DAYS", 90)
TRAINING_MAX_IMAGES = get_int("TRAINING_MAX_IMAGES", 20000)
# Value caps: confident scans of the abundant easy class (healthy) are sampled
# at this rate so a flood of confident-healthy photos can't drown the disease
# corpus; per-class daily cap bounds disk regardless of class.
TRAINING_HEALTHY_KEEP_RATE = get_float("TRAINING_HEALTHY_KEEP_RATE", 0.1)
TRAINING_DAILY_CAP_PER_CLASS = get_int("TRAINING_DAILY_CAP_PER_CLASS", 50)

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
# the guide photos. The artefact is provisioned out of band (never committed);
# when absent the manifest reports available:false and the app falls back to
# the server model. Publish ``aqua/aqua_classifier.tflite`` plus
# ``aqua/classes.json`` from the same verified combined bundle here. The
# endpoint binds both file digests and the combined-bundle identity.
AQUA_STATIC_DIR = Path(get_str(
    "AQUA_STATIC_DIR", str(Path(__file__).resolve().parent / "static" / "aqua")))

# Model artefacts are NOT committed or auto-downloaded. Extract one complete
# notebook bundle tree into MODELS_DIR. Boot is fail-fast on a missing/invalid
# manifest, changed bytes, mixed runs, or a serving-contract mismatch.
MAX_FILE_SIZE = get_int("MAX_FILE_SIZE", 10 * 1024 * 1024)  # 10 MB
# Ceiling on the WHOLE request body, for every route and content type.
# MAX_FILE_SIZE above only ever bound the image field of a multipart upload,
# which left every JSON endpoint (/api/chat, /api/care-plan, /api/community/*,
# /api/telemetry/errors, ...) reading an unbounded body into memory: Starlette
# buffers the body before Pydantic ever sees it, so no field validator can
# refuse it. With --limit-concurrency 64 that is 64 simultaneous bodies of
# whatever size a client feels like sending.
#
# Sized off MAX_FILE_SIZE plus 2 MB of slack, because the largest legitimate
# request IS a multipart image upload: the parts, boundaries and headers
# around the image must fit under this too.
MAX_REQUEST_BYTES = get_int("MAX_REQUEST_BYTES", MAX_FILE_SIZE + 2 * 1024 * 1024)
MAX_IMAGE_DIMENSION = get_int("MAX_IMAGE_DIMENSION", 4096)
PLANTNET_IMAGE_MAX_DIMENSION = get_int("PLANTNET_IMAGE_MAX_DIMENSION", 1024)
# Reject degenerate thumbnails. A tiny upload (e.g. 8x8) carries no
# diagnostic signal but the blur gate passes it (upscaled noise has
# high Laplacian variance) and the detector/classifier then emit a
# confident — and meaningless — verdict (before the severity fix, an 8x8
# noise image scored viral_disease @0.72 with the old fabricated "high"
# severity). Anything below
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
_api_key_feature_receipts: dict[
    tuple[int, str, str, str], EntitlementDecision
] = {}
_MAX_API_KEY_FEATURE_RECEIPTS = 50_000


@dataclass(frozen=True, slots=True)
class _MeteredReservation:
    """One allowance consumed by this request and eligible for safe release.

    ``identity`` is kept only in request-local memory. It is never logged or
    exported as telemetry. Account releases are authoritative in PostgreSQL;
    guest and legacy-key releases update their existing process-local ledgers.
    """

    source: str
    identity: str
    feature: str
    request_id: str
    day: int = 0
    daily_limit: int = 0

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


def _api_key_principal(api_key: str, daily_limit: int) -> tuple[str, int]:
    """Return a non-secret request principal for a validated legacy API key."""

    return hashlib.sha256(api_key.encode("utf-8")).hexdigest(), max(0, daily_limit)


def _consume_api_key_feature(
    *,
    principal_id: str,
    daily_limit: int,
    feature: str,
    request_id: str,
    day: int | None = None,
) -> EntitlementDecision:
    """Consume the legacy key's existing daily quota exactly once per operation.

    Metered routes defer their generic middleware quota increment to this
    function. That lets the same UUID stop an inference replay while retaining
    ``quota_daily`` as the key owner's one authoritative allowance. The
    principal is a digest created only after middleware validated the key; raw
    credentials never enter request state or receipt keys.
    """

    day = int(time.time()) // 86_400 if day is None else day
    day_text = datetime.fromtimestamp(day * 86_400, tz=UTC).strftime("%Y-%m-%d")
    receipt_key = (day, principal_id, feature, request_id)
    reset_at = _quota_reset_at(day)
    with _quota_lock:
        previous = _api_key_feature_receipts.get(receipt_key)
        if previous is not None:
            return EntitlementDecision(
                allowed=previous.allowed,
                replayed=True,
                plan=previous.plan,
                feature=previous.feature,
                limit=previous.limit,
                remaining=previous.remaining,
                reset_at=previous.reset_at,
                reason=previous.reason,
                policy_version=previous.policy_version,
                released=previous.released,
            )

        if len(_api_key_feature_receipts) >= _MAX_API_KEY_FEATURE_RECEIPTS:
            stale = [key for key in _api_key_feature_receipts if key[0] != day]
            for key in stale:
                _api_key_feature_receipts.pop(key, None)
            while len(_api_key_feature_receipts) >= _MAX_API_KEY_FEATURE_RECEIPTS:
                _api_key_feature_receipts.pop(next(iter(_api_key_feature_receipts)))

        day_dict = _in_memory_quotas.setdefault(principal_id, {})
        for old_day in list(day_dict):
            if old_day != day_text:
                day_dict.pop(old_day, None)
        used = day_dict.get(day_text, 0)
        if daily_limit > 0 and used >= daily_limit:
            decision = EntitlementDecision(
                allowed=False,
                replayed=False,
                plan="free",
                feature=feature,
                limit=daily_limit,
                remaining=0,
                reset_at=reset_at,
                reason="quota_exhausted",
            )
        else:
            if daily_limit > 0:
                used += 1
                day_dict[day_text] = used
            decision = EntitlementDecision(
                allowed=True,
                replayed=False,
                plan="free",
                feature=feature,
                limit=daily_limit,
                remaining=max(0, daily_limit - used),
                reset_at=reset_at,
            )
        _api_key_feature_receipts[receipt_key] = decision
        return decision

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
            logger.warning("Temporary in-memory abuse ban applied")

_PUBLIC_COMMUNITY_READ_PATHS = frozenset(
    {
        "/api/community/nearby",
        "/api/community/map",
        "/api/community/heatmap",
        "/api/community/by-species",
    }
)


_PUBLIC_FORUM_READ_PATHS = frozenset(
    {
        "/api/community/feed",
        "/api/community/search",
        "/api/community/spaces",
    }
)


def _is_public_community_read(method: str, path: str) -> bool:
    return method in {"GET", "HEAD"} and (
        path in _PUBLIC_COMMUNITY_READ_PATHS
        or path in _PUBLIC_FORUM_READ_PATHS
        or path.startswith("/api/community/opinions/")
        or path.startswith("/api/community/thumb/")
        or path.startswith("/api/community/posts/")
        or path.startswith("/api/community/media/")
        or path.startswith("/api/community/helpers/")
    )


def _extract_api_key(request: Request, *, allow_bearer: bool = True) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization")
    if allow_bearer and auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def _require_account(request: Request) -> AccountPrincipal:
    token = _bearer_token(request)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="auth_required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await asyncio.get_running_loop().run_in_executor(
            _get_net_executor(), account_authenticator.authenticate, token,
        )
    except AccountAuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="auth_required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except AccountAuthenticationUnavailable:
        raise HTTPException(status_code=503, detail="account_auth_unavailable") from None


async def _optional_account(request: Request) -> AccountPrincipal | None:
    token = _bearer_token(request)
    # The public API historically also accepts opaque API keys in the Bearer
    # slot. Only a compact three-part JWT is an account credential; treating
    # an opaque API key as a broken account login would regress those clients.
    if token is None or token.count(".") != 2:
        return None
    return await _require_account(request)


# Guests retain a useful daily allowance. Signed-in Free/PRO limits live in
# the account database and are consumed atomically by the private service call
# below; these process-local counters are only a budget guard for callers that
# deliberately use the app without an account. They are never persisted.
_GUEST_FEATURE_LIMITS = {
    "gamma_analysis": max(0, get_int("GUEST_GAMMA_ANALYSIS_DAILY_QUOTA", 5)),
    "frontier_analysis": max(0, get_int("GUEST_FRONTIER_ANALYSIS_DAILY_QUOTA", 1)),
    "plant_identification": max(0, get_int("GUEST_IDENTIFY_DAILY_QUOTA", 10)),
    "ai_doctor": max(0, get_int("GUEST_AI_DOCTOR_DAILY_QUOTA", 5)),
    "care_plan": max(0, get_int("GUEST_CARE_PLAN_DAILY_QUOTA", 10)),
}
_guest_feature_lock = threading.Lock()
_guest_feature_counts: dict[tuple[int, str, str], int] = {}
_guest_feature_receipts: dict[tuple[int, str, str, str], EntitlementDecision] = {}
_MAX_GUEST_RECEIPTS = 50_000


def _quota_request_id(request: Request) -> str:
    raw = request.headers.get("x-idempotency-key", "").strip()
    if not raw:
        return str(uuid.uuid4())
    if len(raw) > 64:
        raise HTTPException(status_code=422, detail="invalid_idempotency_key")
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_idempotency_key") from None


def _quota_reset_at(day: int) -> str:
    return datetime.fromtimestamp((day + 1) * 86_400, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _consume_guest_feature(
    *,
    feature: str,
    address: str,
    request_id: str,
    day: int | None = None,
) -> EntitlementDecision:
    limit = _GUEST_FEATURE_LIMITS[feature]
    day = int(time.time()) // 86_400 if day is None else day
    count_key = (day, feature, address)
    receipt_key = (day, feature, address, request_id)
    reset_at = _quota_reset_at(day)
    with _guest_feature_lock:
        previous = _guest_feature_receipts.get(receipt_key)
        if previous is not None:
            return EntitlementDecision(
                allowed=previous.allowed,
                replayed=True,
                plan=previous.plan,
                feature=previous.feature,
                limit=previous.limit,
                remaining=previous.remaining,
                reset_at=previous.reset_at,
                reason=previous.reason,
                policy_version=previous.policy_version,
                released=previous.released,
            )
        if len(_guest_feature_receipts) >= _MAX_GUEST_RECEIPTS:
            stale = [key for key in _guest_feature_receipts if key[0] != day]
            for key in stale:
                _guest_feature_receipts.pop(key, None)
            stale_counts = [key for key in _guest_feature_counts if key[0] != day]
            for key in stale_counts:
                _guest_feature_counts.pop(key, None)
            # A same-day botnet must not make idempotency bookkeeping grow
            # without bound. Keep quota counts but stop remembering the oldest
            # logical requests once the hard cap is reached.
            while len(_guest_feature_receipts) >= _MAX_GUEST_RECEIPTS:
                _guest_feature_receipts.pop(next(iter(_guest_feature_receipts)))
        used = _guest_feature_counts.get(count_key, 0)
        if used >= limit:
            reason = "pro_required" if limit == 0 else "quota_exhausted"
            decision = EntitlementDecision(
                allowed=False,
                replayed=False,
                plan="free",
                feature=feature,
                limit=limit,
                remaining=0,
                reset_at=reset_at,
                reason=reason,
            )
        else:
            used += 1
            _guest_feature_counts[count_key] = used
            decision = EntitlementDecision(
                allowed=True,
                replayed=False,
                plan="free",
                feature=feature,
                limit=limit,
                remaining=max(0, limit - used),
                reset_at=reset_at,
            )
        _guest_feature_receipts[receipt_key] = decision
        return decision


def _released_decision(previous: EntitlementDecision) -> EntitlementDecision:
    """Represent an exact-once local release without enabling UUID reuse."""

    return EntitlementDecision(
        allowed=True,
        replayed=True,
        plan=previous.plan,
        feature=previous.feature,
        limit=previous.limit,
        remaining=min(previous.limit, previous.remaining + 1),
        reset_at=previous.reset_at,
        policy_version=previous.policy_version,
        released=True,
    )


def _release_api_key_feature(reservation: _MeteredReservation) -> bool:
    """Return one unused legacy-key unit while retaining its replay receipt."""

    receipt_key = (
        reservation.day,
        reservation.identity,
        reservation.feature,
        reservation.request_id,
    )
    day_text = datetime.fromtimestamp(
        reservation.day * 86_400,
        tz=UTC,
    ).strftime("%Y-%m-%d")
    with _quota_lock:
        previous = _api_key_feature_receipts.get(receipt_key)
        if previous is None or not previous.allowed or previous.released:
            return False
        if reservation.daily_limit > 0:
            day_dict = _in_memory_quotas.get(reservation.identity)
            if day_dict is None or day_dict.get(day_text, 0) <= 0:
                return False
            day_dict[day_text] -= 1
        _api_key_feature_receipts[receipt_key] = _released_decision(previous)
        return True


def _release_guest_feature(reservation: _MeteredReservation) -> bool:
    """Return one unused guest unit while retaining its replay receipt."""

    count_key = (reservation.day, reservation.feature, reservation.identity)
    receipt_key = (*count_key, reservation.request_id)
    with _guest_feature_lock:
        previous = _guest_feature_receipts.get(receipt_key)
        if previous is None or not previous.allowed or previous.released:
            return False
        if _guest_feature_counts.get(count_key, 0) <= 0:
            return False
        _guest_feature_counts[count_key] -= 1
        _guest_feature_receipts[receipt_key] = _released_decision(previous)
        return True


def _remember_metered_reservation(
    request: Request,
    reservation: _MeteredReservation,
) -> None:
    reservations = getattr(request.state, "metered_reservations", None)
    if not isinstance(reservations, dict):
        reservations = {}
        request.state.metered_reservations = reservations
    reservations[reservation.feature] = reservation


async def _release_metered_feature_if_no_upstream_attempt(
    request: Request,
    *,
    feature: str,
    upstream_attempted: bool | None,
) -> bool:
    """Release this request's allowance only on an explicit no-cost outcome.

    Missing/unknown attempt state retains the allowance. That matters for
    timeouts: the worker may still have reached PlantNet even though the
    request coroutine can no longer observe its result.
    """

    if upstream_attempted is not False:
        return False
    reservations = getattr(request.state, "metered_reservations", None)
    if not isinstance(reservations, dict):
        return False
    reservation = reservations.pop(feature, None)
    if not isinstance(reservation, _MeteredReservation):
        return False
    if reservation.source == "guest":
        return _release_guest_feature(reservation)
    if reservation.source == "api_key":
        return _release_api_key_feature(reservation)
    if reservation.source != "account":
        return False

    client = account_entitlement_client
    if client is None:
        return False
    try:
        decision = await asyncio.get_running_loop().run_in_executor(
            _get_net_executor(),
            functools.partial(
                client.release,
                actor_id=reservation.identity,
                feature=reservation.feature,
                request_id=reservation.request_id,
            ),
        )
    except EntitlementUnavailable:
        # The authority may have committed before the connection failed. Its
        # endpoint is idempotent; retaining the allowance locally is safest
        # when the release outcome cannot be confirmed.
        logger.warning("Entitlement release unavailable")
        return False
    return bool(
        decision.allowed
        and decision.replayed
        and decision.released
        and decision.feature == feature
    )


async def _authorize_metered_feature(
    request: Request,
    *,
    feature: str,
) -> EntitlementDecision:
    request_id = _quota_request_id(request)
    reservation_day = int(time.time()) // 86_400
    legacy_principal = getattr(
        request.state,
        "legacy_api_key_principal",
        None,
    )
    principal = await _optional_account(request)
    if (
        principal is None
        and isinstance(legacy_principal, tuple)
        and len(legacy_principal) == 2
        and isinstance(legacy_principal[0], str)
        and type(legacy_principal[1]) is int
    ):
        decision = _consume_api_key_feature(
            principal_id=legacy_principal[0],
            daily_limit=legacy_principal[1],
            feature=feature,
            request_id=request_id,
            day=reservation_day,
        )
        reservation = _MeteredReservation(
            source="api_key",
            identity=legacy_principal[0],
            feature=feature,
            request_id=request_id,
            day=reservation_day,
            daily_limit=legacy_principal[1],
        )
    elif principal is None:
        address = request.client.host if request.client is not None else "unknown"
        decision = _consume_guest_feature(
            feature=feature,
            address=address,
            request_id=request_id,
            day=reservation_day,
        )
        reservation = _MeteredReservation(
            source="guest",
            identity=address,
            feature=feature,
            request_id=request_id,
            day=reservation_day,
        )
    else:
        client = account_entitlement_client
        if client is None:
            raise HTTPException(status_code=503, detail="entitlement_service_unavailable")
        try:
            decision = await asyncio.get_running_loop().run_in_executor(
                _get_net_executor(),
                functools.partial(
                    client.consume,
                    actor_id=principal.actor_id,
                    feature=feature,
                    request_id=request_id,
                ),
            )
        except EntitlementUnavailable:
            raise HTTPException(
                status_code=503,
                detail="entitlement_service_unavailable",
            ) from None
        reservation = _MeteredReservation(
            source="account",
            identity=principal.actor_id,
            feature=feature,
            request_id=request_id,
        )
    # A retry that reached the quota service once already must not become an
    # unlimited-cost bypass by replaying one UUID while re-running inference.
    # The Android client preserves the UUID only across its pre-consumption
    # 401 refresh retry; a post-consumption replay starts no expensive work.
    if decision.allowed and decision.replayed:
        raise HTTPException(
            status_code=409,
            detail="operation_already_consumed",
            headers={
                "X-PlantDiseases-Plan": decision.plan,
                "X-Quota-Limit": str(decision.limit),
                "X-Quota-Remaining": str(decision.remaining),
                "X-Quota-Reset-At": decision.reset_at,
            },
        )
    if not decision.allowed:
        status_code = 402 if decision.reason == "pro_required" else 429
        raise HTTPException(
            status_code=status_code,
            detail=decision.reason,
            headers={
                "X-PlantDiseases-Plan": decision.plan,
                "X-Quota-Limit": str(decision.limit),
                "X-Quota-Remaining": str(decision.remaining),
                "X-Quota-Reset-At": decision.reset_at,
            },
        )
    _remember_metered_reservation(request, reservation)
    return decision


async def _authorize_optional_species_lookup(request: Request) -> dict | None:
    """Reserve one shared PlantNet unit or describe why fan-out is skipped.

    A disease diagnosis remains useful without its optional species block, so
    quota/replay/account-service failures are represented inside that block
    instead of failing the already-authorized diagnosis.
    """

    if not plantnet_configured():
        return None
    try:
        await _authorize_metered_feature(request, feature="plant_identification")
    except HTTPException as quota_failure:
        if quota_failure.status_code not in {402, 409, 429, 503}:
            raise
        return {
            "available": False,
            "source": {
                402: "pro_required",
                409: "operation_already_consumed",
                429: "quota_exhausted",
                503: "entitlement_unavailable",
            }[quota_failure.status_code],
            "best": None,
            "candidates": [],
        }
    return None


# Daily write quotas use the live connection address only in process memory.
# Addresses and hashes never enter feedback, training, or account-era
# community rows.
FEEDBACK_DAILY_QUOTA = get_int("FEEDBACK_DAILY_QUOTA", 200)
COMMUNITY_DAILY_QUOTA = get_int("COMMUNITY_DAILY_QUOTA", 50)
COMMUNITY_OPINION_DAILY_QUOTA = get_int("COMMUNITY_OPINION_DAILY_QUOTA", 60)
# Likes are cheap and low-stakes compared to an opinion, so they get their
# own (looser) daily cap rather than sharing the opinion quota.
COMMUNITY_LIKE_DAILY_QUOTA = get_int("COMMUNITY_LIKE_DAILY_QUOTA", 300)
# Community roles (expert / moderator / admin) live in the account service.
# ``None`` when unconfigured: staff routes then fail closed with 503.
community_roles = account_roles.community_roles_client
# Levels gate "not helpful", auto-hide reports and label weighting.
community_achievements.install_hooks()


def _forum_maintenance() -> dict:
    """Legacy sync and pruning, consensus labels, then achievements and rewards."""
    forum = community_forum.maintenance()
    labels = community_labels.refresh()
    client = community_roles
    rewards = community_achievements.sync_rewards(
        report=client.report_achievements if client is not None else None
    )
    return {
        **forum,
        "labelled": labels["labelled"],
        "rewards_reported": rewards["reported"],
        "reward_rings": rewards["rings"],
    }


# Forum write budgets, per signed account actor (not per address).
FORUM_POST_DAILY_QUOTA = get_int("FORUM_POST_DAILY_QUOTA", 20)
FORUM_ANSWER_DAILY_QUOTA = get_int("FORUM_ANSWER_DAILY_QUOTA", 150)
FORUM_MEDIA_DAILY_QUOTA = get_int("FORUM_MEDIA_DAILY_QUOTA", 80)
FORUM_AVATAR_DAILY_QUOTA = get_int("FORUM_AVATAR_DAILY_QUOTA", 10)
FORUM_REACTION_DAILY_QUOTA = get_int("FORUM_REACTION_DAILY_QUOTA", 500)
_daily_write_lock = threading.Lock()
_daily_write_counts: dict[tuple[int, str, str], int] = {}


def _consume_daily_write(operation: str, address: str, limit: int) -> bool:
    if limit <= 0:
        return False
    day = int(time.time()) // 86_400
    key = (day, operation, address)
    with _daily_write_lock:
        if len(_daily_write_counts) > 10_000:
            stale = [entry for entry in _daily_write_counts if entry[0] != day]
            for entry in stale:
                _daily_write_counts.pop(entry, None)
        count = _daily_write_counts.get(key, 0)
        if count >= limit:
            return False
        _daily_write_counts[key] = count + 1
        return True

# Daily per-IP allowances on the endpoints that cost money or a shared
# upstream quota. The write quotas above stop spam; these stop one client
# from running up the OpenRouter bill or draining PlantNet's 500-a-day pool
# for everyone, which a 2 req/s bucket alone allows (~170k requests a day).
# Sized far above any real user's day; ``0`` disables one.
ANALYZE_DAILY_QUOTA = get_int("ANALYZE_DAILY_QUOTA", 300)
IDENTIFY_DAILY_QUOTA = get_int("IDENTIFY_DAILY_QUOTA", 150)
# PlantNet calls per IP, shared by the /api/analyze fan-out and /api/identify.
# Past it a scan still gets its diagnosis, without the species block.
PLANTNET_DAILY_QUOTA_PER_IP = get_int("PLANTNET_DAILY_QUOTA_PER_IP", 100)
# AI Plant Doctor turns per IP. The per-session caps in ai_services reset
# with every new client-chosen session_id; this one does not.
CHAT_DAILY_QUOTA = get_int("CHAT_DAILY_QUOTA", 100)
# LLM-polished care plans per IP. Past it the rules-based plan is served.
CARE_PLAN_DAILY_QUOTA = get_int("CARE_PLAN_DAILY_QUOTA", 200)
# /api/species-diseases + /api/species-profile together, per IP. Their caches
# key on client-chosen names, so every new string is a billable miss.
SPECIES_AI_DAILY_QUOTA = get_int("SPECIES_AI_DAILY_QUOTA", 400)


def _client_key(client_ip: str) -> str:
    """The identity the per-client allowances and in-flight caps count.

    An IPv4 address as-is; an IPv6 address by its /64, because one host
    routinely holds a whole /64 and could otherwise mint a fresh identity per
    request. (Carrier NAT runs the other way — many users behind one IPv4 —
    which is why every per-IP default here sits far above one user's day.)
    """
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return client_ip


class _DailyQuota:
    """Per-client counter that resets at UTC midnight.

    In memory and per process, like the rate limiter: a restart forgets the
    day's counts, which only ever errs towards serving. Once ``max_keys``
    clients are tracked, new ones pass untracked (logged once a day) rather
    than grow the map without bound; clients already tracked stay limited.
    Reaching the ceiling takes that many distinct IPv4 addresses or IPv6
    /64s in one day — a botnet, which no per-IP limit stops anyway.
    """

    def __init__(self, name: str, limit: int, *, max_keys: int = 200_000):
        self.name = name
        self.limit = limit
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._day = ""
        self._counts: dict[str, int] = {}
        self._full_logged = False
        self.rejections = 0

    def try_consume(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            if day != self._day:
                self._day = day
                self._counts.clear()
                self._full_logged = False
            used = self._counts.get(key, 0)
            if used >= self.limit:
                self.rejections += 1
                return False
            if used == 0 and len(self._counts) >= self.max_keys:
                if not self._full_logged:
                    self._full_logged = True
                    logger.warning(
                        "Daily %s quota is tracking %d clients; new ones pass "
                        "untracked until UTC midnight", self.name, self.max_keys,
                    )
                return True
            self._counts[key] = used + 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._day = ""
            self._counts.clear()
            self._full_logged = False
            self.rejections = 0


_analyze_quota = _DailyQuota("analyze", ANALYZE_DAILY_QUOTA)
_identify_quota = _DailyQuota("identify", IDENTIFY_DAILY_QUOTA)
_plantnet_quota = _DailyQuota("plantnet", PLANTNET_DAILY_QUOTA_PER_IP)
_chat_quota = _DailyQuota("chat", CHAT_DAILY_QUOTA)
_care_plan_quota = _DailyQuota("care_plan", CARE_PLAN_DAILY_QUOTA)
_species_ai_quota = _DailyQuota("species_ai", SPECIES_AI_DAILY_QUOTA)
_DAILY_QUOTAS = (
    _analyze_quota, _identify_quota, _plantnet_quota,
    _chat_quota, _care_plan_quota, _species_ai_quota,
)


def _require_daily_quota(quota: _DailyQuota, request: Request) -> None:
    """429 once the caller has used up ``quota`` for today (UTC).

    No ``Retry-After``: the app's RetryInterceptor retries a refused POST once
    either way, and with the header it would sleep up to 30 s first only to
    hit the same refusal; without it the retry comes after a 1 s backoff.
    """
    if not quota.try_consume(_client_key(_client_ip(request))):
        logger.warning("Daily %s quota reached for %s", quota.name, request.url.path)
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached for this feature. Try again tomorrow.",
        )


_QUOTA_UNAVAILABLE_TEXT = {
    "en": "You've reached today's limit for this feature. It resets at midnight UTC.",
    "ru": "Дневной лимит для этой функции исчерпан. Он обновится в полночь по UTC.",
}
# Client error/problem telemetry ingestion. Errors-only, opt-out here; the
# cap + retention live in telemetry.py.
TELEMETRY_ENABLED = get_bool("TELEMETRY_ENABLED", True)
TELEMETRY_CONSENT_VERSION = get_str(
    "TELEMETRY_CONSENT_VERSION", "telemetry-errors-v1"
).strip()
TELEMETRY_DAILY_CAP_PER_IP = get_int("TELEMETRY_DAILY_CAP_PER_IP", 200)
TELEMETRY_RETENTION_DAYS = get_int("TELEMETRY_RETENTION_DAYS", 30)
# Retention windows (days). Daily background task in the FastAPI
# lifespan drops rows past this age.
COMMUNITY_RETENTION_DAYS = get_int("COMMUNITY_RETENTION_DAYS", 90)
PRUNE_INTERVAL_SECONDS = get_int("PRUNE_INTERVAL_SECONDS", 24 * 60 * 60)

# Server-issued community publication tokens are separate from feedback
# receipts and expire independently.
COMMUNITY_ANALYSIS_TOKEN_TTL_DAYS = get_int(
    "COMMUNITY_ANALYSIS_TOKEN_TTL_DAYS", 30
)

# One inference at a time by default. Each ONNX session now uses all
# physical cores (ONNX_INTRA_OP_THREADS=0, see classifier.py/detector.py),
# so running two concurrent inferences would oversubscribe the CPU and make
# *both* slower. With the heavy DINOv2 ViT backbone, finishing one scan fast
# beats interleaving two — single-user latency is what matters for this
# rate-limited (1 RPS) workload. Operators expecting real concurrency on a
# many-core host can raise this and lower ONNX_INTRA_OP_THREADS to match.
#
# ONLY ``pipeline.analyze`` runs on this executor. Slow external HTTP
# (PlantNet / OpenRouter / Wikipedia) runs on the network executor below;
# everything else that needs a thread (SQLite reads/writes, image decode /
# hashing, FCM pushes) runs on the event loop's default thread pool —
# putting it here would queue millisecond DB ops behind a 30-second
# inference (head-of-line blocking), stalling every non-scan endpoint
# whenever a scan is in flight.
INFER_WORKERS = get_int("INFER_WORKERS", 1)
_inference_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

# Dedicated pool for calls that WAIT on an external service (OpenRouter,
# PlantNet, Wikipedia — each can legitimately hold a thread for 10–20 s).
# The event loop's default executor is sized from CPU count
# (min(32, cores + 4) = 5 threads on a 1-vCPU host) and is shared by every
# ``run_in_executor(None, …)`` in the app, so a handful of concurrent
# care-plan / species-diseases requests used to occupy ALL of it and queue
# millisecond DB writes behind 20-second LLM waits — a 20 s-capped upstream
# call was observed as 90–110 s of client latency, all of it queueing.
# These threads sleep on network I/O, not CPU, so the pool is sized for
# concurrency, not cores.
NET_WORKERS = get_int("NET_WORKERS", 16)
_net_executor: ThreadPoolExecutor | None = None


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


def _get_net_executor() -> ThreadPoolExecutor:
    """Lazily (re)create the external-network executor (same lifecycle
    contract as ``_get_inference_executor``)."""
    global _net_executor
    with _executor_lock:
        if _net_executor is None or getattr(_net_executor, "_shutdown", False):
            _net_executor = ThreadPoolExecutor(
                max_workers=NET_WORKERS, thread_name_prefix="net",
            )
        return _net_executor

# ── Inference admission control ─────────────────────────────────────
# The inference executor's queue is unbounded, so every upload used to be
# read (up to MAX_FILE_SIZE), decoded and queued with no ceiling: at
# INFER_WORKERS=1 a single client staying inside its rate-limit bucket could
# pin RAM and starve everyone else. /api/analyze and /api/identify now claim
# a slot BEFORE they read or decode anything, and a request that finds the
# server full gets 503 + Retry-After, which the app's RetryInterceptor
# honours (one retry for a POST). The global cap bounds memory — each slot
# holds at most one raw upload plus one decoded image — and the per-IP cap
# stops one client from holding every slot. ``0`` disables either cap.
INFER_MAX_INFLIGHT = get_int("INFER_MAX_INFLIGHT", 4 * max(1, INFER_WORKERS))
INFER_MAX_INFLIGHT_PER_IP = get_int("INFER_MAX_INFLIGHT_PER_IP", 2)
INFER_BUSY_RETRY_AFTER_SECONDS = get_int("INFER_BUSY_RETRY_AFTER_SECONDS", 3)

_admission_lock = threading.Lock()
_inference_inflight = 0
_inference_inflight_by_ip: dict[str, int] = {}
_inference_busy_rejections = 0


def _try_admit_inference(client_ip: str) -> bool:
    """Claim an inference slot for ``client_ip``; False when either cap is full."""
    global _inference_inflight, _inference_busy_rejections
    with _admission_lock:
        per_ip = _inference_inflight_by_ip.get(client_ip, 0)
        if (
            (INFER_MAX_INFLIGHT > 0 and _inference_inflight >= INFER_MAX_INFLIGHT)
            or (INFER_MAX_INFLIGHT_PER_IP > 0 and per_ip >= INFER_MAX_INFLIGHT_PER_IP)
        ):
            _inference_busy_rejections += 1
            return False
        _inference_inflight += 1
        _inference_inflight_by_ip[client_ip] = per_ip + 1
        return True


def _release_inference(client_ip: str) -> None:
    global _inference_inflight
    with _admission_lock:
        _inference_inflight = max(0, _inference_inflight - 1)
        remaining = _inference_inflight_by_ip.get(client_ip, 0) - 1
        if remaining > 0:
            _inference_inflight_by_ip[client_ip] = remaining
        else:
            _inference_inflight_by_ip.pop(client_ip, None)


class _InferenceSlot:
    """A claimed admission slot. ``release()`` is idempotent, so a handler can
    give the slot back as soon as its inference is done — before it waits on
    PlantNet — and the context manager's own release becomes a no-op."""

    def __init__(self, key: str):
        self._key = key
        self._held = True

    def release(self) -> None:
        if self._held:
            self._held = False
            _release_inference(self._key)


@asynccontextmanager
async def _inference_slot(request: Request):
    """Hold one inference slot for the block (or until ``release()``), or 503.

    The slot covers reading, decoding and inference only. Handlers release it
    before any wait on an external service, so a slow upstream cannot turn
    into 503s for everyone while the inference executor sits idle.
    """
    client_ip = _client_key(_client_ip(request))
    if not _try_admit_inference(client_ip):
        logger.warning(
            "Inference busy: rejected %s %s (inflight=%d)",
            request.method, request.url.path, _inference_inflight,
        )
        raise HTTPException(
            status_code=503,
            detail="Server busy - try again in a few seconds",
            headers={"Retry-After": str(INFER_BUSY_RETRY_AFTER_SECONDS)},
        )
    slot = _InferenceSlot(client_ip)
    try:
        yield slot
    finally:
        slot.release()


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


# /api/metrics also reports two store aggregates, each several full-table
# COUNT(*) scans over the training and community databases. They move slowly and
# a dashboard or scraper has no use for them fresher than its own interval, so
# requests inside the TTL share one snapshot instead of each re-scanning the
# tables. 0 disables the cache.
METRICS_SNAPSHOT_TTL_SECONDS = max(0.0, get_float("METRICS_SNAPSHOT_TTL_SECONDS", 5.0))
_metrics_store_snapshot: tuple[float, dict, dict] | None = None


async def _metrics_store_snapshots() -> tuple[dict, dict]:
    """``(training, community)`` aggregates for /api/metrics, at most
    ``METRICS_SNAPSHOT_TTL_SECONDS`` old.

    No lock: two requests that both find the snapshot stale each recompute it,
    which costs what every request cost before and cannot corrupt anything —
    the last writer simply wins.
    """
    global _metrics_store_snapshot
    ttl = METRICS_SNAPSHOT_TTL_SECONDS
    cached = _metrics_store_snapshot
    if ttl > 0 and cached is not None and time.monotonic() - cached[0] < ttl:
        return cached[1], cached[2]
    # Blocking sqlite3, so both hop to a worker thread.
    loop = asyncio.get_running_loop()
    training = await loop.run_in_executor(None, training_store.training_counts)
    community = await loop.run_in_executor(None, community_stats_snapshot)
    _metrics_store_snapshot = (time.monotonic(), training, community)
    return training, community


def _snapshot_metrics() -> dict:
    with _metrics_lock:
        avg = _latency_sum_ms / _latency_count if _latency_count else 0.0
        return {
            "total_requests": _total_requests,
            "total_analyses": _total_analyses,
            "total_errors": _total_errors,
            "avg_latency_ms": round(avg, 1),
            "class_counts": dict(_class_counts),
            "inference_inflight": _inference_inflight,
            "inference_busy_rejections": _inference_busy_rejections,
            "daily_quota_rejections": {q.name: q.rejections for q in _DAILY_QUOTAS},
        }


pipeline: PlantDiseasePipeline | SyntheticPipeline | None = None
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


def _pipeline_for_family(family: str) -> PlantDiseasePipeline | SyntheticPipeline | None:
    """Resolve the pipeline instance that should serve this family."""
    if family == "frontier":
        return pipeline_frontier
    # gamma + aqua (server stand-in) both run on the base pipeline.
    return pipeline


# ── Rate limiter cleanup interval (seconds) ─────────────────────────
_RATE_LIMIT_CLEANUP_INTERVAL = 300


def _persist_community_analysis_token(
    token: str,
    *,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
) -> bool:
    """Persist a publication proof; report failure without leaking a phantom token."""
    try:
        community_record_analysis_token(
            token,
            disease=disease,
            confidence=confidence,
            species=species,
            species_score=species_score,
            image_sha256=image_sha256,
            ttl_days=COMMUNITY_ANALYSIS_TOKEN_TTL_DAYS,
        )
    except Exception:  # noqa: BLE001 - fixed message; never log the token
        logger.warning("Failed to persist community analysis token")
        return False
    return True


async def _issue_interaction_tokens(
    *,
    capture_pending: bool,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
) -> tuple[str | None, str | None]:
    """Mint unrelated community and feedback capabilities for one response."""
    loop = asyncio.get_running_loop()
    community_token = uuid.uuid4().hex
    community_token_persisted = await loop.run_in_executor(
        None,
        lambda: _persist_community_analysis_token(
            community_token,
            disease=disease,
            confidence=confidence,
            species=species,
            species_score=species_score,
            image_sha256=image_sha256,
        ),
    )
    try:
        feedback_receipt = await loop.run_in_executor(
            None,
            lambda: training_store.issue_feedback_receipt(
                capture_pending=capture_pending
            ),
        )
    except Exception:  # noqa: BLE001 - inference remains available, fixed log
        logger.warning("Feedback receipt issuance failed")
        feedback_receipt = None
    return community_token if community_token_persisted else None, feedback_receipt


def _is_trusted_proxy(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when ``ip`` is one of our own reverse-proxy hops."""
    if str(ip) in TRUSTED_PROXIES:
        return True
    for net in TRUSTED_PROXIES:
        try:
            if ip in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:  # malformed TRUSTED_PROXIES entry — ignore it
            continue
    return False


def _client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For only for trusted proxies.

    This value is used only by in-process abuse controls. It and its hash are
    not written to feedback, account-era community rows, or telemetry.

    The header is walked RIGHT-to-LEFT. The reverse proxy documented in
    DEPLOYMENT.md forwards ``$proxy_add_x_forwarded_for``, which *appends* the
    real peer to whatever the client already sent: a request from 198.51.100.9
    carrying a forged ``X-Forwarded-For: 203.0.113.7`` arrives here as
    ``203.0.113.7, 198.51.100.9``. Reading left-to-right therefore returned the
    attacker's chosen value, letting one header spoof an unlimited number of
    distinct identities and so bypass the rate limiter and every per-IP quota.
    Only the rightmost entry that is not itself a trusted hop is trustworthy.
    """
    peer = request.client.host if request.client else "unknown"
    if not TRUSTED_PROXIES:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    # Only trust the header if the direct peer is a configured proxy.
    if not _is_trusted_proxy(peer_ip):
        return peer
    xff = request.headers.get("x-forwarded-for")
    if xff:
        for entry in reversed([e.strip() for e in xff.split(",")]):
            if not entry:
                continue
            try:
                candidate = ipaddress.ip_address(entry)
            except ValueError:
                continue
            if _is_trusted_proxy(candidate):
                continue  # one of our own hops — keep walking leftwards
            return entry
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
        # Health and metadata are free: both are O(1) reads of in-memory
        # state, and a client that cannot poll /api/health cannot tell "server
        # down" from "you are throttled".
        #
        # /api/metrics* is NOT on this list any more. Those two handlers run
        # full-table SQLite aggregates (feedback_counts, community_stats) on
        # the event loop, so an exemption made the single most expensive
        # unauthenticated request in the API also the only unmetered one — a
        # free amplifier, one cheap GET to many table scans. They are ordinary
        # rate-limited endpoints now; at the default 2 req/s a Prometheus
        # scrape every 15 s is nowhere near the bucket.
        path = request.url.path
        if path in ("/api/health", "/api/capabilities"):
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
                "Received X-Forwarded-For but TRUSTED_PROXIES is empty - "
                "every client behind the proxy shares one rate-limit bucket. "
                "Set TRUSTED_PROXIES to the proxy CIDR to honour XFF."
            )

        client_ip = _client_ip(request)
        bearer = _bearer_token(request)
        public_community_read = _is_public_community_read(request.method, path)
        internal_service_path = path in {
            "/api/internal/account-deletions",
            "/api/internal/account-nicknames",
        }
        protected_account_path = bool(
            path.startswith("/api/community/") and not public_community_read
        )
        metered_account_path = path in {
            "/api/analyze",
            "/api/identify",
            "/api/chat",
            "/api/care-plan",
        }
        compact_account_bearer = bool(
            bearer and bearer.count(".") == 2 and metered_account_path
        )
        # Public reads intentionally ignore both account credentials and API
        # keys. Protected account routes reserve Authorization for the JWT.
        # The metered AI routes additionally accept a compact account JWT;
        # opaque Bearer values remain legacy API keys everywhere else.
        api_key = (
            None
            if public_community_read or protected_account_path or internal_service_path
            else _extract_api_key(request, allow_bearer=not compact_account_bearer)
        )
        account_token_allowed = bool(
            bearer
            and (protected_account_path or compact_account_bearer)
        )

        # 1. API Key validation and requirement check
        if REQUIRE_API_KEY and not public_community_read:
            if not api_key and not account_token_allowed and not internal_service_path:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required. Provide via X-API-Key or Authorization header."},
                )
            if api_key and api_key not in API_KEYS:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API key."},
                )
        elif not public_community_read and api_key and api_key not in API_KEYS:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key."},
            )

        # 2. Determine identifier and limits
        if api_key:
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
            principal_id, quota_daily = _api_key_principal(api_key, quota_daily)
            identifier = f"key:{principal_id}"
        else:
            identifier = f"ip:{client_ip}"
            rate = self.rate
            capacity = self.capacity
            quota_daily = 0
            principal_id = None

        # Metered endpoints consume the validated legacy key's daily quota at
        # the feature boundary, after request validation. This also gives them
        # UUID replay protection; all other routes retain the historical
        # middleware-level generic API-key quota.
        deferred_metered_key_quota = bool(principal_id and metered_account_path)
        if principal_id is not None:
            request.state.legacy_api_key_principal = (principal_id, quota_daily)

        # 3. Check Banned state (abuse control)
        if _is_banned(identifier):
            return JSONResponse(
                status_code=403,
                content={"detail": "API key or IP temporarily banned due to suspected abuse."},
            )

        # Bypass rate limiter checks if rate is infinity or non-positive
        if rate == float("inf") or rate <= 0:
            # 4. Daily quota still applies to unthrottled keys.
            if (
                quota_daily > 0
                and not deferred_metered_key_quota
                and not _check_and_increment_quota(principal_id, quota_daily)
            ):
                logger.warning("API-key daily quota reached")
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
                logger.warning("Request rate limit reached")
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
        if (
            quota_daily > 0
            and not deferred_metered_key_quota
            and not _check_and_increment_quota(principal_id, quota_daily)
        ):
            logger.warning("API-key daily quota reached")
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


class BodySizeLimitMiddleware:
    """Refuse request bodies over ``max_bytes`` with 413.

    Deliberately a raw ASGI middleware rather than a ``BaseHTTPMiddleware``:
    the cap has to act on the byte stream itself, and ``BaseHTTPMiddleware``
    only hands over a request whose body downstream code will buffer anyway.

    Two layers, because either one alone is bypassable:

    1. A declared ``Content-Length`` over the cap is refused before a single
       body byte is read. This is the path every ordinary client takes.
    2. Otherwise the stream is metered as it is consumed, which is what
       catches ``Transfer-Encoding: chunked`` — where the sender simply never
       declares a length. Passing the cap ends the stream and the 413 is
       written instead of the application's response.

    Sitting outside the rate limiter is intentional: an oversized body should
    cost as little as possible to reject, and should not spend the client's
    token bucket on a request that never reached a route.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    async def _send_413(send) -> None:
        body = json.dumps({"detail": "Request body too large"}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for key, value in scope.get("headers", ()):
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break  # malformed — fall through to stream metering
                if declared > self.max_bytes:
                    await self._send_413(send)
                    return
                break

        state = {"received": 0, "over": False, "started": False}

        async def metered_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > self.max_bytes:
                    state["over"] = True
                    # Ending the stream is what unblocks the route; it then
                    # sees a disconnected client instead of hanging.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            # Suppress the application's own response only while we can still
            # replace it. Once the first byte is out the die is cast.
            if state["over"] and not state["started"]:
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, metered_receive, guarded_send)
        except Exception:
            # A route that raises because we cut its body short must not
            # surface as a 500 — the client's oversized body is the error.
            if not (state["over"] and not state["started"]):
                raise
        if state["over"] and not state["started"]:
            await self._send_413(send)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log only a route template and coarse request outcome."""

    async def dispatch(self, request: Request, call_next):
        # Never accept a client trace identifier: it can be stable across
        # requests and become an undeclared installation/account correlate.
        trace_id = uuid.uuid4().hex
        token = request_id_var.set(trace_id)
        # Also stash it on the request scope. The ContextVar is reset by the
        # `finally` below before Starlette's outermost ServerErrorMiddleware
        # gets to run `_unhandled_exception_handler`, but `request.state` is
        # backed by the scope dict and survives — so that handler can stamp
        # the SAME trace id on the 500 that this log line carries.
        request.state.trace_id = trace_id
        _record_request()
        start = time.time()
        try:
            response: StarletteResponse = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            elapsed = time.time() - start
            route = request.scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            logger.info(
                "REQUEST %s %s -> %d (%.3fs)",
                request.method,
                route_template,
                response.status_code,
                elapsed,
            )
            return response
        except Exception:
            # An unhandled handler exception never reaches the success path
            # above, so without this arm the access log simply has no line for
            # the request that failed. Log it as the 500 the client will get
            # (`_unhandled_exception_handler` produces the response), then
            # re-raise so that handler still runs.
            elapsed = time.time() - start
            logger.info(
                "REQUEST %s %s -> %d (%.3fs) IP=%s",
                request.method,
                request.url.path,
                500,
                elapsed,
                _client_ip(request),
            )
            raise
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
    global pipeline, pipeline_frontier, SERVER_START_TIME, _inference_executor, _net_executor
    SERVER_START_TIME = time.time()
    _inference_executor = _get_inference_executor()
    _net_executor = _get_net_executor()
    training_store.init_db()
    init_community_db()
    init_telemetry_db()
    # One complete notebook-produced bundle must already be present under
    # MODELS_DIR. The constructor verifies its manifest, hashes, lineage and
    # serving contract before loading the exact selected graphs.
    if ACCOUNT_BETA_SYNTHETIC_PIPELINE:
        pipeline = SyntheticPipeline()
        logger.warning("Synthetic test-only inference pipeline enabled")
    else:
        pipeline = PlantDiseasePipeline(
            detector_path=DETECTOR_PATH,
            classifier_path=CLASSIFIER_PATH,
            require_bundle_manifest=True,
        )
    logger.info(
        "Pipeline initialised (classifier=%s, %d classes, family=%s)",
        pipeline.classifier.backend,
        pipeline.classifier.NUM_CLASSES,
        pipeline.classifier.model_family,
    )
    # Frontier is opt-in and carries its own fully bound detector/classifier
    # tree. Unlike gamma, a missing/broken frontier bundle is
    # NOT fail-fast: we log and leave ``pipeline_frontier=None`` so the
    # always-on gamma path still serves. The picker / analyze gate then
    # report frontier unavailable. This keeps "enabled in config but
    # artefacts not provisioned yet" from taking the whole server down.
    if FRONTIER_ENABLED and not ACCOUNT_BETA_SYNTHETIC_PIPELINE:
        try:
            pipeline_frontier = PlantDiseasePipeline(
                detector_path=FRONTIER_DETECTOR_PATH,
                classifier_path=FRONTIER_CLASSIFIER_PATH,
                require_bundle_manifest=True,
            )
            logger.info(
                "Frontier pipeline initialised (classifier=%s, %d classes, family=%s)",
                pipeline_frontier.classifier.backend,
                pipeline_frontier.classifier.NUM_CLASSES,
                pipeline_frontier.classifier.model_family,
            )
        except Exception:  # noqa: BLE001 — frontier must degrade, not abort boot
            pipeline_frontier = None
            logger.error(
                "Enabled frontier pipeline failed to load; frontier is unavailable"
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
    warm_task = asyncio.create_task(_warm_placeholder_images()) if POPULAR_WARM_ENABLED else None
    try:
        yield
    finally:
        logger.info("Server shutting down")
        prune_task.cancel()
        if warm_task is not None:
            warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prune_task
        if warm_task is not None:
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
            if _net_executor is not None:
                # In-flight external calls drain within their own budgets
                # (OPENROUTER_TOTAL_TIMEOUT / PLANTNET_TIMEOUT).
                _net_executor.shutdown(wait=True, cancel_futures=True)
                _net_executor = None


async def _prune_loop() -> None:
    """Background scheduler: prune training + community rows past
    retention every ``PRUNE_INTERVAL_SECONDS``. First pass runs
    immediately so a restart catches up on stale rows.
    """
    while True:
        try:
            training_pruned = await asyncio.get_running_loop().run_in_executor(
                None,
                training_store.prune_training_data,
                TRAINING_RETENTION_DAYS,
                TRAINING_MAX_IMAGES,
            )
            removed_cm = await asyncio.get_running_loop().run_in_executor(
                None, community_prune_older_than, COMMUNITY_RETENTION_DAYS,
            )
            cleanup = await asyncio.get_running_loop().run_in_executor(
                None, community_process_cleanup_jobs,
            )
            forum = await asyncio.get_running_loop().run_in_executor(
                _get_net_executor(), _forum_maintenance,
            )
            removed_tm = await asyncio.get_running_loop().run_in_executor(
                None, telemetry_prune_older_than, TELEMETRY_RETENTION_DAYS,
            )
            if (
                training_pruned["samples"]
                or training_pruned["receipts"]
                or removed_cm
                or removed_tm
                or cleanup["completed"]
                or cleanup["retried"]
                or any(forum.values())
            ):
                logger.info(
                    "Prune pass: training=%d/%d community=%d telemetry=%d cleanup=%d/%d "
                    "forum=%s",
                    training_pruned["samples"], training_pruned["receipts"],
                    removed_cm, removed_tm,
                    cleanup["completed"], cleanup["retried"],
                    forum,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Prune pass failed; it will retry")
        try:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


app = FastAPI(
    title="PlantDiseases API",
    description="AI-powered plant disease detection \u2014 two-stage pipeline",
    version=APP_VERSION,
    root_path=APP_ROOT_PATH,
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Own the unhandled-exception path so a crash is still observable.

    Without this, an exception escaping a handler is turned into a response by
    Starlette's ServerErrorMiddleware: a bare text/plain "Internal Server
    Error" with NO ``X-Trace-ID`` header (that is only set on the success path
    in :class:`RequestLoggingMiddleware`) and no metrics increment — so the one
    request the operator most needs to find is the one with nothing to grep
    for. Here we count it, log it with a traceback under the request's trace
    id, and hand back the same JSON ``{"detail": ...}`` shape every other error
    on this API uses, carrying the trace id the access log line also shows.

    The handler runs OUTSIDE every middleware added below, so ``request_id_var``
    has already been reset by the time we get here; the id comes off
    ``request.state`` (stamped by the logging middleware, and backed by the
    scope dict so it survives). The fallback covers an exception raised in a
    middleware layer outside the logging one, which never got stamped.

    The detail string is deliberately generic — an exception message can carry
    a path, a query fragment or a key, and this is the one error the client did
    not ask a question specific enough to deserve an answer to. The trace id is
    the operator's join key into the log.

    Starlette re-raises after this returns, so uvicorn still logs the traceback
    and ``TestClient(raise_server_exceptions=True)`` still surfaces it in tests.
    """
    _record_error()
    # Server-minted only: RequestLoggingMiddleware deliberately ignores any
    # client-supplied trace id, so this must not reintroduce one via headers.
    trace_id = getattr(request.state, "trace_id", "") or uuid.uuid4().hex
    logger.exception(
        "UNHANDLED %s %s trace=%s: %s",
        request.method,
        request.url.path,
        trace_id,
        exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={"X-Trace-ID": trace_id},
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
# Outside the rate limiter, inside CORS: an oversized body is rejected before
# it can be buffered, but the 413 still carries Access-Control-* headers so a
# browser client reads the status instead of an opaque CORS failure.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
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


def _metadata_free_external_jpeg(image: Image.Image) -> bytes:
    """Return bounded RGB pixels with no source metadata for third parties.

    PlantNet only needs the visible plant pixels.  Rebuilding a fresh image
    before encoding prevents EXIF/GPS, ICC, XMP, IPTC, thumbnails, comments,
    and source filenames from crossing the service boundary even when a
    non-Android client calls the public API directly.
    """
    bounded_side = max(64, min(PLANTNET_IMAGE_MAX_DIMENSION, MAX_IMAGE_DIMENSION))
    resized = image.convert("RGB").copy()
    if max(resized.size) > bounded_side:
        resized.thumbnail((bounded_side, bounded_side), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", resized.size)
    clean.paste(resized)
    encoded = io.BytesIO()
    clean.save(
        encoded,
        format="JPEG",
        quality=88,
        optimize=True,
        progressive=False,
        subsampling=2,
    )
    return encoded.getvalue()


def _sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest. Pulled out so the analyze handler can run it in
    a thread — hashing up to MAX_UPLOAD_BYTES on the event loop would stall
    every other request for the duration."""
    return hashlib.sha256(data).hexdigest()


# ── Consent-gated, de-identified model-improvement capture ───────────


def _record_popular_contribution(species: str | None, disease: str) -> None:
    """Count one accepted diagnosis in the anonymous worldwide popularity
    tally (species + class only; see community.record_anonymous_scan).
    Fail-soft: never affects the response."""
    try:
        community_record_anonymous_scan(species, disease)
    except Exception:  # noqa: BLE001 - background bookkeeping only
        logger.warning("Popular tally update failed")


def _record_training_candidate(
    image: Image.Image,
    feedback_receipt: str,
    *,
    family: str,
    model_version: str,
    result: dict,
    consent_version: str,
) -> None:
    """Store one detector-backed leaf crop after the response, fail-soft."""
    stored = False
    try:
        class_name = str(result.get("class_name") or "")
        if not TRAINING_CAPTURE_ENABLED or _derive_abstention(result)[0]:
            return
        detection = result.get("detection") or {}
        safe_crop = training_store.extract_safe_leaf_crop(image, detection)
        if safe_crop is None:
            return
        crop, area_buckets = safe_crop
        sanitized = training_store.sanitize_training_image(crop)
        prediction_set = list(
            (result.get("prediction_set") or {}).get("classes") or []
        )
        confidence = float(result.get("confidence") or 0.0)
        review_status = training_store.route_model_review(
            class_name,
            confidence,
            prediction_set,
            trust_conformal=TRAINING_TRUST_CONFORMAL,
            confidence_threshold=TRAINING_CONF_HIGH,
        )
        if (
            review_status == "auto_yes"
            and class_name not in DISEASE_CLASSES
            and not training_store.deterministically_sample(
                sanitized.sha256, TRAINING_HEALTHY_KEEP_RATE
            )
        ):
            return
        if (
            training_store.count_samples_for_day(class_name)
            >= TRAINING_DAILY_CAP_PER_CLASS
        ):
            return
        training_store.store_sample(
            sanitized,
            source="consented_scan",
            model_family=family,
            model_version=model_version,
            predicted_class=class_name,
            predicted_confidence=confidence,
            top_k=list(result.get("top_k") or []),
            conformal_set=prediction_set,
            warnings=list(result.get("warnings") or []),
            blur_bucket=training_store.blur_score_bucket(image),
            detector_region_count=len(detection.get("regions") or []),
            bbox_area_buckets=area_buckets,
            severity=str(result.get("severity") or "unknown"),
            review_status=review_status,
            consent_version=consent_version,
            receipt=feedback_receipt,
        )
        stored = True
    except Exception:  # noqa: BLE001 - background capture never affects inference
        logger.warning("Training capture failed")
    finally:
        if not stored:
            try:
                training_store.complete_receipt_without_sample(feedback_receipt)
            except Exception:  # noqa: BLE001 - fixed message, no receipt logging
                logger.warning("Training receipt finalization failed")


# ── Endpoints ────────────────────────────────────────────────────────

api_router = APIRouter(prefix="/api")


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


def _apply_abstention_presentation(payload: dict) -> dict:
    """Apply the public rejection contract without changing diagnostics.

    ``class_name`` / ``confidence`` / ``all_probs`` / ``top_k`` remain the raw
    probability-backed classifier diagnostics. OOD and quality rejections are
    not accepted diagnoses, so every downstream claim (disease care, species,
    pet toxicity, conformal set and disease flag) is neutralised. A natural
    trained-head ``not_a_plant`` keeps its established NAP copy.
    """
    abstained, reason = _derive_abstention(payload)
    payload["abstained"] = abstained
    payload["abstention_reason"] = reason
    payload["verdict_status"] = "abstained" if abstained else "accepted"
    if not abstained:
        return payload

    detection = dict(payload.get("detection") or {})
    detection["is_diseased"] = False
    payload["detection"] = detection
    payload["prediction_set"] = None
    payload["is_healthy"] = False
    payload["severity"] = "low"
    payload["toxic_to_pets"] = False
    payload["toxic_species_list"] = []
    payload["toxicity_source"] = "unknown"
    payload["species"] = {
        "available": False,
        "source": (
            "skipped_nap" if reason == _ABSTENTION_REASON_NAP
            else "skipped_abstained"
        ),
        "best": None,
        "candidates": [],
    }

    if reason != _ABSTENTION_REASON_NAP:
        neutral = DISEASES_DATABASE["unknown"]
        payload["disease_name"] = neutral["name_en"]
        payload["disease_name_ru"] = neutral["name_ru"]
        payload["description"] = neutral["description_en"]
        payload["description_ru"] = neutral["description_ru"]
        payload["treatment"] = list(neutral["treatment_en"])
        payload["treatment_ru"] = list(neutral["treatment_ru"])
        # Prevention/care advice is a diagnosis-dependent claim. Keep only the
        # explicit retake actions above until an image is accepted.
        payload["prevention"] = []
        payload["prevention_ru"] = []
    return payload


_PROM_LABEL_ESCAPE = str.maketrans({"\\": r"\\", "\"": r"\"", "\n": r"\n"})


def _prom_label(value: str) -> str:
    return value.translate(_PROM_LABEL_ESCAPE)


async def _species_identify_best_effort(
    image_bytes: bytes,
    *,
    timeout: float,
    lang: str = "en",
    cached: dict | None = None,
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

    Takes the decoded image, not the upload bytes: PlantNet receives a
    metadata-free re-encode (``_plantnet_upload_bytes``), so EXIF/GPS from
    a client that did not strip it never reaches the third party.
    """
    loop = asyncio.get_running_loop()
    try:
        raw = cached
        if raw is None:
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    _get_net_executor(),
                    # identify_species keyword args (organ, lang) are
                    # keyword-only — run_in_executor only takes positional
                    # args, so we wrap in a lambda. Passing positionally
                    # raises TypeError at call time and surfaces as a 500.
                    lambda: identify_species(
                        image_bytes, organ="auto", lang=lang,
                    ),
                ),
                timeout=timeout,
            )
    except (TimeoutError, asyncio.CancelledError):
        logger.info("Species identify skipped (timeout / cancelled)")
        return None
    except Exception:  # noqa: BLE001
        logger.info("Species identify failed")
        return None
    upstream_attempted = (
        raw.get("_upstream_attempted")
        if isinstance(raw, dict) and type(raw.get("_upstream_attempted")) is bool
        else None
    )
    if not raw or not raw.get("available"):
        unavailable = {
            "available": False,
            "source": raw.get("source", "unavailable") if raw else "unavailable",
            "best": None,
            "candidates": [],
        }
        if upstream_attempted is not None:
            unavailable["_upstream_attempted"] = upstream_attempted
        return unavailable
    candidates = raw.get("results") or []
    if not candidates:
        no_candidates = {
            "available": True,
            "source": raw.get("source", "plantnet"),
            "best": None,
            "candidates": [],
        }
        if upstream_attempted is not None:
            no_candidates["_upstream_attempted"] = upstream_attempted
        return no_candidates
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
    shaped = {
        "available": True,
        "source": raw.get("source", "plantnet"),
        "best": _shape(candidates[0]),
        "candidates": [_shape(c) for c in candidates[:3]],
    }
    if upstream_attempted is not None:
        shaped["_upstream_attempted"] = upstream_attempted
    return shaped


def build_analyze_payload(
    result: dict,
    *,
    scan_id: str | None,
    species_payload: dict | None,
    species_override: str | None,
    served_family: str,
    requested_family: str,
    family_fallback_reason: str | None,
) -> dict:
    """Assemble the ``/api/analyze`` response from one pipeline ``result``.

    Pure — no I/O, no awaits — so the response shape (the bilingual top-k, the
    advisory conformal set and its dual care cards, the abstention-driven
    species suppression, the toxicity fall-through and the abstention
    presentation) is unit-testable without a TestClient. The handler keeps
    everything that talks to the outside world: inference, the PlantNet wait,
    minting the per-response tokens, the cache and the scan recording.
    ``scan_id`` is omitted from the payload when ``None``.

    ``species_payload`` is the resolved PlantNet block (``None`` when the
    handler cancelled it); ``species_override`` is the ``?species=`` hint,
    which drives the toxicity lookup only.
    """
    class_name = result["class_name"]
    confidence = result["confidence"]
    all_probs = result.get("all_probs", {})
    warnings = list(result.get("warnings", []))
    top_k = list(result.get("top_k", []))
    abstained, abstention_reason = _derive_abstention(result)
    disease_info = DISEASES_DATABASE.get(class_name, DISEASES_DATABASE["unknown"])

    rounded_probs = {k: round(v, 4) for k, v in all_probs.items()}

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
    is_healthy_effective = (
        not abstained and bool(disease_info.get("is_healthy", False))
    )

    # Conformal prediction set: classes the model can't rule out at the
    # configured coverage level. Annotated with bilingual names so the
    # UI can render an advisory "leaf_spot OR viral_disease" set with its
    # nominal target clearly distinguished from measured coverage.
    # without a second lookup table. ``None`` when calibration data
    # isn't yet available — clients must treat it as optional.
    prediction_set = None if abstained else result.get("prediction_set")
    if prediction_set is not None:
        # Calibration-row coverage is not an independent validation. Keep the
        # set advisory until a locked, family-independent served-path test
        # establishes coverage for this exact graph + detector/crop/vote
        # contract; no per-class-count heuristic can substitute for that.
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

    # PlantNet on a non-plant photo always returns a low-quality guess
    # ("Phaseolus vulgaris" because there was some green pixel noise)
    # which is actively misleading on the result screen. When the
    # pipeline lands on ``not_a_plant`` we suppress the species block
    # entirely — the LRU cache in ``identify_species`` keeps the API
    # call from being wasted on a repeat upload of the same image.
    if abstained:
        species_payload = {
            "available": False,
            "source": (
                "skipped_nap"
                if abstention_reason == _ABSTENTION_REASON_NAP
                else "skipped_abstained"
            ),
            "best": None, "candidates": [],
        }

    # Pet-toxicity surface — resolves from the explicit ``species``
    # query param when supplied, otherwise falls back to the auto-
    # detected best match. Without either we return
    # ``source="unknown"`` and the client renders an "unconfirmed"
    # copy rather than falsely reassuring.
    auto_best = (species_payload or {}).get("best") or {}
    toxicity_species = None if abstained else (species_override or auto_best.get("scientific_name"))
    toxicity = lookup_pet_toxicity(toxicity_species) if toxicity_species else {
        "toxic_to_pets": False, "species_list": [], "source": "unknown",
    }

    response_payload = {
        # Probability-backed classifier argmax. Rejection gates never replace
        # it: clients use ``abstained`` / ``abstention_reason`` to decide
        # whether this diagnostic may be presented as an accepted diagnosis.
        "class_name": class_name,
        # Which model tier actually produced this verdict. Lets the client
        # confirm it got the family it asked for (and fall back gracefully if
        # a future server downgrades the request).
        "family": served_family,
        "requested_family": requested_family,
        "family_fallback_reason": family_fallback_reason,
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
        "severity": result.get("severity", "low"),
        "all_probs": rounded_probs,
        "top_k": top_k_localised,
        "warnings": warnings,
        "abstained": abstained,
        "abstention_reason": abstention_reason,
        "verdict_status": "abstained" if abstained else "accepted",
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
    }
    if scan_id is not None:
        # Server-minted token for this one response. The handler passes None
        # for the cacheable payload and adds its own capabilities afterwards,
        # so a token is never replayed from the diagnosis cache.
        response_payload["scan_id"] = scan_id
    return _apply_abstention_presentation(response_payload)


async def _plantnet_quota_exhausted() -> dict:
    """Species block for a scan whose IP has used up its PlantNet allowance."""
    return {"available": False, "source": "quota", "best": None, "candidates": []}


async def _analyze_image_admitted(
    request: Request,
    background_tasks: BackgroundTasks,
    image: UploadFile,
    slot: _InferenceSlot,
    *,
    client_ip: str,
    species: str | None,
    locale: str,
    family: str,
    training_consent: bool,
    training_consent_version: str | None,
) -> JSONResponse:
    """The body of ``/api/analyze``, run while holding an inference slot."""
    if pipeline is None:
        _record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")

    consent_version = (training_consent_version or "").strip().lower()
    expected_consent_version = TRAINING_CONSENT_VERSION.lower()
    if training_consent and consent_version != expected_consent_version:
        raise HTTPException(status_code=422, detail="training_consent_version_invalid")

    family = _normalise_family(family)
    requested_family = family
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
    classifier_meta = getattr(serving_pipeline.classifier, "meta", {})
    model_version = str(
        (classifier_meta or {}).get("model_version")
        or ("synthetic-test" if ACCOUNT_BETA_SYNTHETIC_PIPELINE else "unknown")
    )
    served_family_raw = getattr(serving_pipeline.classifier, "model_family", None)
    served_family = (
        str(served_family_raw).strip().lower()
        if isinstance(served_family_raw, str) and served_family_raw.strip()
        else DEFAULT_FAMILY
    )
    family_fallback_reason = (
        None
        if served_family == requested_family
        else (
            "aqua_served_by_gamma"
            if requested_family == "aqua" and served_family == "gamma"
            else "requested_family_fallback"
        )
    )

    start_time = time.time()

    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        _record_error()
        logger.warning("Analyze upload type rejected")
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Allowed: JPEG, PNG, WebP, BMP",
        )

    try:
        contents = await _read_upload(image)
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        _record_error()
        logger.warning("Analyze upload read failed")
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
        logger.warning("Analyze image validation failed")
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc

    img_w, img_h = img.size
    if img_w > MAX_IMAGE_DIMENSION or img_h > MAX_IMAGE_DIMENSION:
        _record_error()
        logger.warning("Analyze image dimensions rejected")
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too large ({img_w}x{img_h}). "
                   f"Maximum dimension: {MAX_IMAGE_DIMENSION}px",
        )
    if min(img_w, img_h) < MIN_IMAGE_DIMENSION:
        _record_error()
        logger.warning("Analyze image dimensions rejected")
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too small ({img_w}x{img_h}). "
                   f"Minimum dimension: {MIN_IMAGE_DIMENSION}px - please retake the photo.",
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
        # Capabilities are fresh per response and must never be shared through
        # the diagnosis cache, even when two callers upload identical bytes.
        cached_payload.pop("scan_id", None)
        cached_payload.pop("feedback_receipt", None)
        cached_payload["is_repeat"] = True
        cached_payload["cached_at"] = cache_hit["cached_at"]
        cached_payload["elapsed_ms"] = 0
        cached_payload["family"] = served_family
        cached_payload["requested_family"] = requested_family
        cached_payload["family_fallback_reason"] = family_fallback_reason
        _apply_abstention_presentation(cached_payload)
        species_override = (species or "").strip() or None
        if species_override and not cached_payload["abstained"]:
            overridden = lookup_pet_toxicity(species_override)
            cached_payload["toxic_to_pets"] = overridden["toxic_to_pets"]
            cached_payload["toxic_species_list"] = overridden["species_list"]
            cached_payload["toxicity_source"] = overridden["source"]
        capture_pending = bool(
            TRAINING_CAPTURE_ENABLED
            and training_consent
            and not cached_payload["abstained"]
        )
        cached_species = (cached_payload.get("species") or {}).get("best") or {}
        community_token, feedback_receipt = await _issue_interaction_tokens(
            capture_pending=capture_pending,
            disease=(
                "not_a_plant" if cached_payload["abstained"]
                else str(cached_payload.get("class_name") or "")
            ),
            confidence=cached_payload.get("confidence"),
            species=cached_species.get("scientific_name"),
            species_score=cached_species.get("score"),
            image_sha256=image_sha,
        )
        cached_payload["scan_id"] = community_token
        cached_payload["feedback_receipt"] = feedback_receipt
        if capture_pending and feedback_receipt is not None:
            background_tasks.add_task(
                _record_training_candidate,
                img.copy(),
                feedback_receipt,
                family=family,
                model_version=model_version,
                result=copy.deepcopy(cached_payload),
                consent_version=consent_version,
            )
        return JSONResponse(content=cached_payload)

    # Only uncached work spends a daily allowance: a repeated photo is served
    # from the cache above for free. Malformed or unsupported photos were
    # rejected earlier, so exactly one metered operation is reserved here,
    # before inference.
    await _authorize_metered_feature(
        request,
        feature=("frontier_analysis" if family == "frontier" else "gamma_analysis"),
    )

    # A first-time diagnosis also offers a best-effort PlantNet species block.
    # That is a real vendor identification call, so it shares the *same*
    # plant-identification allowance as the explicit /api/identify surface.
    # Without this second authorization, 30 PRO Gamma scans plus 3 explicit
    # identifies could silently multiply paid vendor calls even though the
    # launch product contract caps Plant identification at 3/day for PRO.
    # Cached diagnosis responses never reach this branch and therefore spend
    # neither a user identification unit nor a vendor call.
    species_task: asyncio.Task | None = None
    species_payload: dict | None = None
    species_authorized = False
    species_image_bytes = await loop.run_in_executor(
        None,
        _metadata_free_external_jpeg,
        img.copy(),
    )
    cached_species = await loop.run_in_executor(
        None,
        functools.partial(
            plantnet_cache_lookup,
            species_image_bytes,
            organ="auto",
            lang=plantnet_lang,
        ),
    )
    if cached_species is None and not _plantnet_quota.try_consume(client_ip):
        # This IP has used its daily PlantNet allowance: the scan still gets
        # its diagnosis, without the species block. A cache hit costs no
        # lookup and is never counted.
        species_payload = await _plantnet_quota_exhausted()
    elif cached_species is None and plantnet_configured():
        species_payload = await _authorize_optional_species_lookup(request)
        species_authorized = species_payload is None
    if species_payload is None:
        # Fire PlantNet in parallel with the disease pipeline. PlantNet remains
        # best-effort: timeout/provider failure yields an unavailable species
        # block while the diagnosis succeeds. The 10s cap matches one full
        # configured HTTP attempt and stays below the inference timeout.
        species_task = asyncio.create_task(
            _species_identify_best_effort(
                species_image_bytes,
                timeout=10.0,
                lang=plantnet_lang,
                cached=cached_species,
            )
        )
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_get_inference_executor(), serving_pipeline.analyze, img),
            timeout=INFERENCE_TIMEOUT,
        )
    except TimeoutError as exc:
        if species_task is not None:
            species_task.cancel()
        _record_error()
        logger.error("Inference timed out")
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
        if species_task is not None:
            species_task.cancel()
        _record_error()
        logger.error("Inference failed")
        raise HTTPException(status_code=500, detail="Internal inference error") from exc
    # Inference is done: give the slot back before waiting on PlantNet.
    slot.release()

    class_name = result["class_name"]
    confidence = result["confidence"]
    abstained, abstention_reason = _derive_abstention(result)

    elapsed = time.time() - start_time
    metric_class = (
        f"abstained:{abstention_reason or _ABSTENTION_REASON_UNKNOWN}"
        if abstained else class_name
    )
    _record_analysis(metric_class, elapsed * 1000)

    logger.info("Analysis completed")

    # Resolve the species fan-out. We started it in parallel with the
    # disease pipeline so this normally returns immediately. If the
    # caller already supplied a ``species`` query param (e.g. user
    # picked one earlier), it overrides the auto-detected scientific
    # name when looking up pet toxicity, but we keep the auto-detected
    # block in the response either way so the client can show the
    # match confidence.
    if species_task is not None and abstained:
        species_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await species_task
    elif species_task is not None:
        try:
            species_payload = await species_task
        except Exception:  # noqa: BLE001 — defensive; helper already swallows
            species_payload = None

    species_upstream_attempted = (
        species_payload.get("_upstream_attempted")
        if isinstance(species_payload, dict)
        and type(species_payload.get("_upstream_attempted")) is bool
        else None
    )
    if species_authorized:
        await _release_metered_feature_if_no_upstream_attempt(
            request,
            feature="plant_identification",
            upstream_attempted=species_upstream_attempted,
        )
    if isinstance(species_payload, dict):
        species_payload.pop("_upstream_attempted", None)

    # The shared builder applies the abstention species suppression, the
    # toxicity lookup and the response shape. ``scan_id=None`` keeps the
    # cacheable payload free of per-response capabilities; they are minted
    # below from the species block the builder settled on.
    cached_response_payload = build_analyze_payload(
        result,
        scan_id=None,
        species_payload=species_payload,
        species_override=species,
        served_family=served_family,
        requested_family=requested_family,
        family_fallback_reason=family_fallback_reason,
    )
    auto_best = (cached_response_payload["species"] or {}).get("best") or {}

    capture_pending = bool(
        TRAINING_CAPTURE_ENABLED
        and training_consent
        and not abstained
    )
    community_token, feedback_receipt = await _issue_interaction_tokens(
        capture_pending=capture_pending,
        # A rejected image keeps its raw argmax in ``class_name`` but is not a
        # diagnosis: bind the publication proof to the hidden NAP class so an
        # abstained scan can never surface in the community feed as a disease.
        disease="not_a_plant" if abstained else class_name,
        confidence=round(confidence, 3),
        species=auto_best.get("scientific_name"),
        species_score=auto_best.get("score"),
        image_sha256=image_sha,
    )
    # Stash the freshly-built response under its (image_hash,
    # plantnet_lang) key so the NEXT identical upload from the same
    # locale short-circuits at the top of this handler.
    # Not when this caller's PlantNet allowance blanked the species block: the
    # cache would replay that blank to everyone uploading the same bytes.
    if (cached_response_payload.get("species") or {}).get("source") != "quota":
        _analyze_cache_put(cache_key, cached_response_payload)
    response_payload = {
        **cached_response_payload,
        # This server-minted token is accepted only by community publication.
        "scan_id": community_token,
        # This unrelated 256-bit capability is accepted only by feedback.
        "feedback_receipt": feedback_receipt,
    }
    if capture_pending and feedback_receipt is not None:
        background_tasks.add_task(
            _record_training_candidate,
            img.copy(),
            feedback_receipt,
            family=family,
            model_version=model_version,
            result=copy.deepcopy(result),
            consent_version=consent_version,
        )
    # Every user with the onboarding consent counts towards the worldwide
    # popular lists, with or without location: one anonymous species+class
    # tally per fresh, accepted diagnosis (repeats of a photo are served from
    # the cache above and are not counted again).
    if training_consent and not abstained:
        background_tasks.add_task(
            _record_popular_contribution, auto_best.get("scientific_name"), class_name,
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
    # What the plan is for. ``scan``: adapted to a diagnosis (the scan result
    # and the plant it was saved to); a fresh AI summary spends the daily
    # care-plan allowance. ``species``: a species' general care (species
    # page), always the healthy-plant plan and never metered. ``checklist``:
    # the structured plan behind a garden treatment checklist, no AI summary,
    # never metered.
    purpose: Literal["scan", "species", "checklist"] = "scan"


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


def _to_public_feed_item(
    row: dict, *, opinions: dict, views: dict | None = None,
) -> dict:
    """Make one community-feed row safe to hand to a viewer (in place).

    Every viewer-facing feed does the same four things, and forgetting any one
    of them is a leak rather than a cosmetic bug — which is why they live here
    instead of being retyped per handler:

    1. annotate the row with its second-opinion count (and, on the feeds that
       show it, its view count) so the card can badge it;
    2. swap the real ``scan_id`` for the public token — the real id authorises
       ``/api/feedback`` writes and must never reach another user (see
       ``community.public_scan_token``);
    3. drop the private keyset-pagination fields the query attaches;
    4. localize the country code so cards show "Poland"/"Польша", not "PL".

    Rows that already carry a count (the recommended ranking computes its own
    in SQL) keep it. ``views=None`` means this feed does not expose a view
    count, so the key is not added.
    """
    sid = str(row.get("scan_id"))
    row.setdefault("opinion_count", opinions.get(sid, 0))
    if views is not None:
        row.setdefault("view_count", views.get(sid, 0))
    row["scan_id"] = row.pop("public_id", None) or row["scan_id"]
    row.pop("_cursor_score", None)
    row.pop("_cursor_row_id", None)
    _localize_item_country(row)
    return row


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
    country: str | None = Field(default=None, min_length=2, max_length=80)
    region: str = Field(default="", max_length=80)
    disease: str = Field(..., min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    species: str | None = Field(default=None, max_length=128)
    species_score: float | None = Field(default=None, ge=0.0, le=1.0)
    locale: str | None = Field(default=None, max_length=8)
    @field_validator("scan_id")
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


# The app sends its 12 newest scans; the cap leaves room for that to grow
# while bounding the work a single request can hand the event loop.
PROGRESSION_MAX_HISTORY = 50


class ProgressionRequest(BaseModel):
    """Time-ordered list of scans (oldest first) for the disease-
    progression sparkline + trend the plant-detail screen renders."""

    history: list[dict] = Field(..., max_length=PROGRESSION_MAX_HISTORY)


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
# v2.2 head doesn't carry yet, plus a structured catch-all key. We accept
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
    # "The reject was wrong — there IS a plant here." Sent by the app's
    # single-action feedback on a not_a_plant / blur verdict; these rows
    # (plus their uploaded images) are exactly what the rejection head
    # needs for retraining.
    "is_a_plant",
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

# Opaque server capabilities use a URL-safe alphabet. Community still names
# its publication capability ``scan_id`` for API compatibility; feedback uses
# a separate receipt that is never accepted by community endpoints.
_SCAN_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class FeedbackRequest(BaseModel):
    """One structured correction authorized by an unlinkable receipt."""

    model_config = ConfigDict(extra="forbid")

    feedback_receipt: str = Field(..., min_length=43, max_length=43)
    correct: bool
    corrected_class: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=32)

    @field_validator("feedback_receipt")
    @classmethod
    def _safe_receipt(cls, value: str) -> str:
        if any(ch not in _SCAN_ID_ALLOWED for ch in value):
            raise ValueError("invalid feedback receipt")
        return value

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

    @model_validator(mode="after")
    def _consistent_correction(self) -> "FeedbackRequest":
        if self.correct and self.corrected_class is not None:
            raise ValueError("corrected_class is only valid for negative feedback")
        return self


async def _identify_admitted(
    request: Request,
    image: UploadFile,
    slot: _InferenceSlot,
    *,
    client_ip: str,
    organ: str,
    locale: str,
) -> JSONResponse:
    """The body of ``/api/identify``, run while holding an inference slot."""
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
        logger.warning("Identify upload read failed")
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

    # Decode and validate the pixels before sending anything to PlantNet. The
    # NAP filter is the same detector + classifier pipeline as /api/analyze,
    # so it must see the same EXIF-normalised RGB image and run on the same
    # bounded inference executor (never on the event loop or network pool).
    try:
        img = await loop.run_in_executor(None, _open_image, contents)
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        _record_error()
        logger.exception("Unexpected error while decoding /api/identify upload: %s", exc)
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc

    img_w, img_h = img.size
    if img_w > MAX_IMAGE_DIMENSION or img_h > MAX_IMAGE_DIMENSION:
        _record_error()
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too large ({img_w}x{img_h}). "
                   f"Maximum dimension: {MAX_IMAGE_DIMENSION}px",
        )
    if min(img_w, img_h) < MIN_IMAGE_DIMENSION:
        _record_error()
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too small ({img_w}x{img_h}). "
                   f"Minimum dimension: {MIN_IMAGE_DIMENSION}px - please retake the photo.",
        )

    try:
        nap_result = await asyncio.wait_for(
            loop.run_in_executor(_get_inference_executor(), pipeline.analyze, img),
            timeout=INFERENCE_TIMEOUT,
        )
    except TimeoutError as exc:
        _record_error()
        logger.error("NAP filter timed out after %.0fs for /api/identify", INFERENCE_TIMEOUT)
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
        _record_error()
        logger.exception("NAP filter crashed for /api/identify: %s", exc)
        raise HTTPException(status_code=500, detail="Internal inference error") from exc

    # The NAP gate was the only inference here; PlantNet waits slot-free.
    slot.release()
    if nap_result.get("class_name") == "not_a_plant":
        return JSONResponse(content={
            "available": False,
            "results": [],
            "source": "skipped_nap",
            "organ": organ,
        })

    # Re-encode from the already-decoded image: a direct API client must not be
    # able to bypass the Android uploader's sanitizer and hand PlantNet EXIF
    # (including GPS) that never needed to leave the device.
    try:
        external_image = await loop.run_in_executor(
            None,
            lambda: _metadata_free_external_jpeg(img),
        )
    except HTTPException:
        _record_error()
        raise
    except Exception as exc:
        _record_error()
        logger.warning("Identify image validation failed")
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc
    cached_result = await loop.run_in_executor(
        None,
        functools.partial(
            plantnet_cache_lookup,
            external_image,
            organ=organ,
            lang=plantnet_lang,
        ),
    )
    identification_authorized = False
    if cached_result is None:
        # Per-IP allowance on PlantNet lookups (a cache hit spends none),
        # checked before the account's metered budget is touched.
        if not _plantnet_quota.try_consume(client_ip):
            return JSONResponse(content={
                "available": False,
                "results": [],
                "source": "quota",
                "organ": organ,
            })
        if plantnet_configured():
            await _authorize_metered_feature(request, feature="plant_identification")
            identification_authorized = True
    try:
        result = cached_result
        if result is None:
            result = await loop.run_in_executor(
                _get_net_executor(),
                lambda: identify_species(
                    external_image,
                    organ=organ,
                    lang=plantnet_lang,
                ),
            )
    except HTTPException:
        _record_error()
        raise
    upstream_attempted = (
        result.get("_upstream_attempted")
        if isinstance(result, dict)
        and type(result.get("_upstream_attempted")) is bool
        else None
    )
    if identification_authorized:
        await _release_metered_feature_if_no_upstream_attempt(
            request,
            feature="plant_identification",
            upstream_attempted=upstream_attempted,
        )
    # Deep-copy result before merging so a cache-hit return from
    # ``identify_species`` (which now also deep-copies, but defence in
    # depth is cheap) doesn't share its nested ``results`` /
    # ``common_names`` lists with the JSON response — middleware mutation
    # would otherwise corrupt the PlantNet LRU entry for the next reader.
    public_result = copy.deepcopy(result)
    public_result.pop("_upstream_attempted", None)
    return JSONResponse(content={**public_result, "organ": organ})


# ── Community scan corpus ───────────────────────────────────────────


# Same content-type and magic-byte set the analyze endpoint accepts. We rely on
# Content-Type for the multipart form parse, then re-validate the
# bytes; an attacker can lie about Content-Type but the magic bytes
# don't lie.
_COMMUNITY_ALLOWED_TYPES = set(ALLOWED_CONTENT_TYPES)


class InternalActorDeletionRequest(BaseModel):
    actor_id: str = Field(..., min_length=36, max_length=36)


class InternalActorNicknameRequest(BaseModel):
    actor_id: str = Field(..., min_length=36, max_length=36)
    nickname: str = Field(..., min_length=1, max_length=48)


class TelemetryErrorRequest(BaseModel):
    """One consented report containing only allowlisted technical enums."""

    model_config = ConfigDict(extra="forbid")

    telemetry_consent: bool = False
    telemetry_consent_version: str | None = Field(default=None, max_length=32)
    stage: str = Field(..., min_length=1, max_length=32)
    kind: str = Field(..., min_length=1, max_length=32)
    fatal: bool = False
    http_status: int | None = Field(default=None, ge=100, le=599)
    family: str | None = Field(default=None, max_length=16)
    app_version: str | None = Field(default=None, max_length=32)
    android_api: int | None = Field(default=None, ge=1, le=100)
    breadcrumbs: list[str] | None = Field(default=None, max_length=20)

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, value: str) -> str:
        if value not in TELEMETRY_ALLOWED_STAGES:
            raise ValueError("stage is not allowlisted")
        return value

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in TELEMETRY_ALLOWED_KINDS:
            raise ValueError("kind is not allowlisted")
        return value

    @field_validator("family")
    @classmethod
    def _known_family(cls, value: str | None) -> str | None:
        if value is not None and value not in TELEMETRY_ALLOWED_FAMILIES:
            raise ValueError("family is not allowlisted")
        return value

    @field_validator("app_version")
    @classmethod
    def _safe_app_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(
            character not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._+-"
            for character in value
        ):
            raise ValueError("app_version is invalid")
        return value

    @field_validator("breadcrumbs")
    @classmethod
    def _known_breadcrumbs(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if any(value not in TELEMETRY_ALLOWED_BREADCRUMBS for value in values):
            raise ValueError("breadcrumb is not allowlisted")
        return values


class CommunityOpinionRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)
    suggested_disease: str = Field(..., min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class CommunityReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: str = Field(..., min_length=4, max_length=7)
    target_id: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(..., min_length=1, max_length=32)


class CommunityBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: str = Field(..., min_length=4, max_length=7)
    target_id: str = Field(..., min_length=1, max_length=128)


def _raise_community_safety_error(error: ValueError) -> None:
    code = str(error)
    if code == "report_limit":
        raise HTTPException(status_code=429, detail=code) from None
    if code in {"self_target", "target_not_blockable", "block_limit"}:
        raise HTTPException(status_code=409, detail=code) from None
    if code == "deleted account actor":
        raise HTTPException(status_code=403, detail="account_deleted") from None
    if code in {
        "invalid_actor",
        "invalid_reason",
        "target_not_found",
    }:
        raise HTTPException(status_code=422, detail=code) from None
    raise HTTPException(status_code=500, detail="community_safety_failed") from None


class CommunityRequestOpinionRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)


class CommunityViewRequest(BaseModel):
    # ``scan_id`` (singular) is the original single-open shape, kept required
    # in name only — older installs still send it alone. ``scan_ids`` is the
    # newer batch shape for reporting many feed impressions in one call. A
    # request must carry at least one of the two; see the validator below.
    scan_id: str | None = Field(None, max_length=128)
    scan_ids: list[str] | None = Field(None, max_length=50)

    @model_validator(mode="after")
    def _require_a_target(self) -> "CommunityViewRequest":
        if not (self.scan_id or "").strip() and not any(
            (s or "").strip() for s in (self.scan_ids or [])
        ):
            raise ValueError("scan_id or scan_ids is required")
        return self


class CommunityLikeRequest(BaseModel):
    scan_id: str = Field(..., min_length=1, max_length=128)
    liked: bool = True


_COMMUNITY_REPORT_REASONS = {
    "sexual_content",
    "violence_or_threats",
    "hate_or_harassment",
    "illegal_or_dangerous",
    "spam_or_misleading",
    "privacy_or_impersonation",
    "other_rules_violation",
}


class CommunityScanCountsRequest(BaseModel):
    scan_ids: list[str] = Field(..., max_length=500)


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
_WEBP_MAX_SOURCE_SIDE = 16_384
_WEBP_MAX_SOURCE_PIXELS = 20_000_000


def _webp_source_dimensions_allowed(src: Path) -> bool | None:
    """Return False only for a source that violates the decode-size policy.

    ``None`` preserves the historical raw-file fallback for unrelated Pillow
    failures. Oversized sources, including Pillow's decompression-bomb error,
    must never be served as that fallback.
    """
    try:
        with Image.open(src) as opened:
            width, height = opened.size
    except Image.DecompressionBombError:
        return False
    except Exception:  # noqa: BLE001 - caller may safely use the old fallback
        return None
    return (
        width > 0
        and height > 0
        and max(width, height) <= _WEBP_MAX_SOURCE_SIDE
        and width * height <= _WEBP_MAX_SOURCE_PIXELS
    )


def _webp_variant(
    src: Path,
    max_side: int,
    quality: int,
    after_write=None,
) -> Path | None:
    """Return a cached downscaled-WebP variant of ``src`` (``max_side`` px on
    the longest edge, ``quality`` 0–100), generating it if stale.

    Any failure (PIL hiccup, unwritable dir) returns ``None``. Callers may fall
    back to the original after enforcing their own source-safety policy.
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
            with Image.open(src) as opened:
                w, h = opened.size
                if (
                    w <= 0
                    or h <= 0
                    or max(w, h) > _WEBP_MAX_SOURCE_SIDE
                    or w * h > _WEBP_MAX_SOURCE_PIXELS
                ):
                    raise ValueError("image dimensions exceed WebP source limit")
                opened.load()
                img = opened.convert("RGB")
            scale = max_side / float(max(w, h))
            if scale < 1.0:
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS,
                )
            tmp = out.with_suffix(".webp.tmp")
            img.save(tmp, format="WEBP", quality=quality, method=4)
            tmp.replace(out)
            if after_write is not None:
                after_write()
        return out
    except Exception:  # noqa: BLE001 — fall back to the original
        logger.warning("WebP transcode failed")
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


def _valid_aqua_classes(meta: object) -> bool:
    """Return whether an Aqua sidecar is installable by the Android runtime.

    Keep this contract intentionally narrow and mirrored in
    ``AquaModelManager.validClasses``.  Reporting a model as available with a
    merely parseable sidecar causes a guaranteed client-side install failure,
    so missing preprocessing or output semantics fail closed here.
    """
    if not isinstance(meta, dict):
        return False
    if any(
        key in meta
        for key in (
            "_model_sha256",
            "_classes_sha256",
            "_bundle_sha256",
            "_model_version",
        )
    ):
        return False
    names = meta.get("class_names")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name or name.strip() != name for name in names)
        or len(set(names)) != len(names)
    ):
        return False
    if meta.get("model_family") != "aqua":
        return False
    version = meta.get("model_version")
    if not isinstance(version, str) or not version.strip():
        return False

    training = meta.get("training")
    if not isinstance(training, dict):
        return False
    img_size = training.get("img_size")
    resize_ratio = training.get("eval_resize_ratio")
    if isinstance(img_size, bool) or not isinstance(img_size, int) or not 1 <= img_size <= 4096:
        return False
    if (
        isinstance(resize_ratio, bool)
        or not isinstance(resize_ratio, (int, float))
        or not math.isfinite(float(resize_ratio))
        or not 1.0 <= float(resize_ratio) <= 4.0
    ):
        return False

    inference = meta.get("inference")
    if not isinstance(inference, dict):
        return False
    if (
        inference.get("layout") != "NHWC"
        or inference.get("tta") != "none"
        or inference.get("output_semantics") != "logits"
    ):
        return False

    def _valid_triple(key: str, *, positive: bool) -> bool:
        values = inference.get(key)
        if not isinstance(values, list) or len(values) != 3:
            return False
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            number = float(value)
            if not math.isfinite(number) or (positive and number <= 0.0):
                return False
        return True

    return _valid_triple("normalize_mean", positive=False) and _valid_triple(
        "normalize_std", positive=True,
    )


def _aqua_classes_sha256(meta: dict) -> str:
    """Hash the sidecar's JSON meaning, independent of whitespace/key order."""
    canonical = json.dumps(
        meta,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _aqua_bundle_sha256(model_sha256: str, classes_sha256: str) -> str:
    """Bind the model and semantic sidecar into one freshness identity."""
    digest = hashlib.sha256()
    digest.update(b"plantvision-aqua-bundle-v1\0")
    digest.update(bytes.fromhex(model_sha256))
    digest.update(bytes.fromhex(classes_sha256))
    return digest.hexdigest()


# --- /api/popular: personalized, short-cached popular-plants bundle ----
# ``_popular_cache_key(...)`` -> (expiry_epoch, payload). The list is a
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


def _popular_cache_key(
    country: str | None, lat, lon, locale: str,
) -> str:
    """Cache key for one popular bundle scope.

    Shared by the reader (:func:`popular_endpoint`) and the boot-time
    pre-warm, which used to build the key by hand in a different shape and so
    filed its warmed bundles under slots nothing could ever read."""
    return f"{country or ''}|{lat or ''},{lon or ''}|{locale}"


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
                    _get_net_executor(), lambda s=p.scientific, n=lang: fetch_species_info(s, n),
                )
            # Pre-cache the lead photo bytes too, so the very first carousel
            # request serves images instantly from our disk cache.
            url = (info or {}).get("image_url")
            if url and url not in warmed_photos:
                warmed_photos.add(url)
                with contextlib.suppress(Exception):
                    await loop.run_in_executor(
                        _get_net_executor(), lambda u=url: plant_photo_get_or_fetch(u),
                    )
    for locale in ("en", "ru"):
        with contextlib.suppress(Exception):
            bundle = await loop.run_in_executor(
                _get_net_executor(), lambda n=locale: _build_popular_bundle(None, None, None, None, n),
            )
            key = _popular_cache_key(None, None, None, locale)
            with _popular_lock:
                _popular_cache[key] = (time.time() + _popular_ttl(bundle), bundle)
                _popular_cache.move_to_end(key)


def _worldwide_snapshot() -> dict:
    """Worldwide popularity: the anonymous tally of every consented, accepted
    diagnosis (community members included, location or not); the community
    corpus only while the tally is still empty (e.g. right after deploy)."""
    anonymous = community_anonymous_popular_snapshot(limit=_POPULAR_MAX_CARDS)
    if anonymous.get("top_species") or anonymous.get("top_diseases"):
        return anonymous
    return community_popular_snapshot(limit=_POPULAR_MAX_CARDS)


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
        s = _worldwide_snapshot()
        if _has(s):
            snap, source = s, "worldwide"

    # A local (nearby) snapshot we can't attach a real place to — no
    # resolvable country AND no city — would be labeled "Worldwide" by
    # ``_country_display(None)`` below and rendered as "Popular in Worldwide"
    # over what is actually a single local scan. Show the true worldwide
    # aggregate instead so the label matches the data. (Worldwide always has
    # ≥ the local rows, so this never empties a populated feed.)
    if source == "nearby" and not country and not region_label:
        s = _worldwide_snapshot()
        if _has(s):
            snap, source, region_label = s, "worldwide", None

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
                # Always resolve the ENGLISH Wikipedia lead image regardless of
                # UI locale: the localized (e.g. RU) article often has a
                # different or missing lead photo, which made two distinct
                # species look identical in one locale but not another. Titles
                # stay localized; only the picture is pinned to EN.
                "image_url": plant_photo_name_proxy(p.scientific, "en"),
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
    used_titles: set[str] = set()
    for row in (snap.get("top_species") or [])[:_POPULAR_MAX_CARDS]:
        scientific = row["species"]
        # Only the localized title needs resolving here (cached); the photo is
        # resolved + cached on demand by the by-name proxy, so a species that
        # only just trended (a fresh scan) still gets an image.
        info = fetch_species_info(scientific, lang)
        title = (info or {}).get("title") or scientific
        # A localized title can collapse two distinct species to one name (e.g.
        # RU Wikipedia titles both "Spathiphyllum" and "Spathiphyllum
        # floribundum" as "Спатифиллюм"), which shows two identical-looking
        # cards. On a collision fall back to the scientific name so each card
        # stays distinguishable.
        if title in used_titles:
            title = scientific
        used_titles.add(title)
        items.append({
            "scientific": scientific,
            "common_en": title,
            "common_ru": title,
            # English lead image for every locale — see the placeholder branch.
            "image_url": plant_photo_name_proxy(scientific, "en"),
            "count": row.get("count", 0),
            "percent": row.get("percent", 0),
        })
    # A thin community corpus (a new region, or one with a handful of scans)
    # would otherwise leave a one- or two-card carousel that never seems to
    # change. Top it up from the curated list for the same scope: the real
    # scans stay first with their percentages, the curated picks follow with
    # zero counts (the client shows those without a percentage).
    seen = {it["scientific"].lower() for it in items}
    for p in popular_for(country if source != "worldwide" else None):
        if len(items) >= _POPULAR_MAX_CARDS:
            break
        if p.scientific.lower() in seen:
            continue
        seen.add(p.scientific.lower())
        items.append({
            "scientific": p.scientific,
            "common_en": p.common_en,
            "common_ru": p.common_ru,
            "image_url": plant_photo_name_proxy(p.scientific, "en"),
            "count": 0,
            "percent": 0,
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
                _get_net_executor(), lambda: _build_popular_bundle(country, city, lat, lon, locale),
            )
            with _popular_lock:
                _popular_cache[key] = (time.time() + _popular_ttl(bundle), bundle)
                _popular_cache.move_to_end(key)
                while len(_popular_cache) > _POPULAR_CACHE_MAX:
                    _popular_cache.popitem(last=False)
        except Exception:  # noqa: BLE001 — background best-effort
            logger.info("Popular background refresh failed")
        finally:
            with _popular_lock:
                _popular_refreshing.discard(key)

    with contextlib.suppress(RuntimeError):  # no running loop (shouldn't happen)
        asyncio.create_task(_run())


# The route handlers live in the ``routes_*`` modules beside this file. Each
# ``register`` adds its routes straight onto ``api_router`` (a nested router
# would drop the ``/api`` prefix from the route template the access log
# prints) and gets this module, through which the handlers resolve helpers,
# state and collaborators at call time — so patching a name here (as the
# tests do) or reloading this module reaches every route. The handlers are
# also bound here under their own names, where callers have always found them.
for _routes in (
    routes_meta,
    routes_diagnosis,
    routes_assistant,
    routes_internal,
    routes_community,
    routes_forum,
    routes_moderation,
    routes_content,
):
    _registered = len(api_router.routes)
    _routes.register(api_router, sys.modules[__name__])
    for _route in api_router.routes[_registered:]:
        globals()[_route.endpoint.__name__] = _route.endpoint


app.include_router(api_router)


# Direct-run convenience only. The documented deployment paths (start.sh,
# start.bat, the systemd unit in DEPLOYMENT.md) all exec ``uvicorn main:app``
# and pass the host/port/forwarded-allow-ips as CLI flags from the shell
# environment — so HOST / PORT / FORWARDED_ALLOW_IPS below are read *solely*
# by ``python main.py``. Setting them in config.json has no effect anywhere
# else.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        # The API is intentionally reachable on the container network; the
        # reviewed ingress/firewall owns public exposure and TLS termination.
        host=get_str("HOST", "0.0.0.0"),  # nosec B104
        port=get_int("PORT", 8000),
        forwarded_allow_ips=get_str("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
