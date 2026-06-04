"""
PlantDiseases Server — FastAPI backend with the PlantVision two-stage
houseplant disease pipeline.

Stage 1:  YOLOv8n              — locate plant leaves (single class)
Stage 2:  MobileNetV3-Large    — distilled student (KD from
                                 EfficientNetV2-S teacher), classifies
                                 cropped leaf regions; mean-voted
                                 across bboxes, ONNX (INT8 dynamic
                                 on MatMul).
"""

import asyncio
import contextlib
import copy
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

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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
    from .ai_services import (  # type: ignore[import-not-found]
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
    )
    from .community import (
        COMMUNITY_THUMB_DIR,
    )
    from .community import (
        daily_write_count_for_ip_hash as community_daily_writes,
    )
    from .community import (
        delete_for_user as community_delete_for_user,
    )
    from .community import (
        get_user_data as community_get_user_data,
    )
    from .community import (
        init_db as init_community_db,
    )
    from .community import (
        nearby_scans as community_nearby_scans,
    )
    from .community import (
        prune_older_than as community_prune_older_than,
    )
    from .community import (
        record_scan as community_record_scan,
    )
    from .community import (
        region_summary as community_region_summary,
    )
    from .community import (
        stats_snapshot as community_stats_snapshot,
    )
    from .config import get, get_float, get_int, get_list, get_str
    from .diseases_data import DISEASES_DATABASE, lookup_pet_toxicity
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
        prune_older_than as feedback_prune_older_than,
    )
    from .pipeline import PlantDiseasePipeline
except ImportError:
    from ai_services import (
        chat_metrics_snapshot,
        chat_reply,
        compare_progression_history,
        generate_care_plan,
        identify_species,
    )
    from community import (
        COMMUNITY_THUMB_DIR,
    )
    from community import (
        daily_write_count_for_ip_hash as community_daily_writes,
    )
    from community import (
        delete_for_user as community_delete_for_user,
    )
    from community import (
        get_user_data as community_get_user_data,
    )
    from community import (
        init_db as init_community_db,
    )
    from community import (
        nearby_scans as community_nearby_scans,
    )
    from community import (
        prune_older_than as community_prune_older_than,
    )
    from community import (
        record_scan as community_record_scan,
    )
    from community import (
        region_summary as community_region_summary,
    )
    from community import (
        stats_snapshot as community_stats_snapshot,
    )
    from config import get, get_float, get_int, get_list, get_str
    from diseases_data import DISEASES_DATABASE, lookup_pet_toxicity
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
        prune_older_than as feedback_prune_older_than,
    )
    from pipeline import PlantDiseasePipeline

def _resolve_app_version() -> str:
    """Resolve the served model/app version.

    Prefer the package ``__version__``; when the server is run as a flat
    module (``main:app`` in the Docker image) that import fails, so fall
    back to the shipped model metadata (``models/classes.json ->
    model_version``) rather than a hardcoded string that silently lags
    the deployed model. Only if both are unavailable do we use a final
    constant.
    """
    try:
        from . import __version__ as pkg_version  # type: ignore

        return str(pkg_version)
    except Exception:
        pass
    try:
        meta = json.loads(
            (Path("models") / "classes.json").read_text(encoding="utf-8")
        )
        version = meta.get("model_version")
        if version:
            return str(version)
    except Exception:
        pass
    return "2.2"


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
ANALYZE_CACHE_TTL_SECONDS = get_int("ANALYZE_CACHE_TTL_SECONDS", 3600)
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


RATE_LIMIT_RPS = get_float("RATE_LIMIT_RPS", 1.0)
# Burst capacity on top of the steady-state rate. A burst of 5 lets
# the client pop off 5 requests back-to-back (e.g. nav + analyze +
# care-plan + identify + feedback fan-out) before the token bucket
# stops refilling — without that the analyze flow used to slam into
# the limiter on every cold open. Default chosen to absorb a single
# scan + its post-result side calls.
RATE_LIMIT_BURST = get_float("RATE_LIMIT_BURST", 5.0)
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

VIOLATION_THRESHOLD = 5
VIOLATION_WINDOW = 60.0
BAN_DURATION = 300.0

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
# Retention windows (days). Daily background task in the FastAPI
# lifespan drops rows past this age.
FEEDBACK_RETENTION_DAYS = get_int("FEEDBACK_RETENTION_DAYS", 180)
COMMUNITY_RETENTION_DAYS = get_int("COMMUNITY_RETENTION_DAYS", 90)
PRUNE_INTERVAL_SECONDS = get_int("PRUNE_INTERVAL_SECONDS", 24 * 60 * 60)

# Server-issued scan_ids stay valid for this long. Long enough that
# a user can scan today and leave feedback in a week; short enough
# that a leaked id can't be reused indefinitely.
ISSUED_SCAN_ID_TTL_DAYS = get_int("ISSUED_SCAN_ID_TTL_DAYS", 30)

INFER_WORKERS = get_int("INFER_WORKERS", 2)
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

# ── Rate limiter cleanup interval (seconds) ─────────────────────────
_RATE_LIMIT_CLEANUP_INTERVAL = 300


def _issue_scan_id() -> str:
    """Mint a fresh URL-safe scan_id, persist it in the issued log,
    and return it for the /analyze response. Run inside the request
    handler so callers don't need to think about the DB hop.
    """
    scan_id = uuid.uuid4().hex
    try:
        record_issued_scan_id(scan_id)
    except Exception as exc:  # noqa: BLE001 — never fail analyse on the log write
        logger.warning("Failed to persist issued scan_id: %s", exc)
    return scan_id


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
        if request.url.path in (
            "/api/health",
            "/api/version",
            "/api/classes",
            "/api/metrics",
            "/api/metrics/prometheus",
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
            key_cfg = API_KEYS[api_key]
            rate = float(key_cfg.get("rate_limit_rps", self.rate))
            capacity = float(key_cfg.get("burst", self.capacity))
            quota_daily = int(key_cfg.get("quota_daily", 0))
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

        # 4. Check Daily Quota (if key has quota)
        if quota_daily > 0:
            if not _check_and_increment_quota(api_key, quota_daily):
                logger.warning("Daily quota exceeded for key=%s quota=%d", api_key[:12], quota_daily)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Daily quota exceeded for this API key. Try again tomorrow."},
                )

        # Bypass rate limiter checks if rate is infinity or non-positive
        if rate == float("inf") or rate <= 0:
            return await call_next(request)

        # 5. Token bucket rate limit check
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
    global pipeline, SERVER_START_TIME, _inference_executor
    SERVER_START_TIME = time.time()
    _inference_executor = _get_inference_executor()
    init_feedback_db()
    init_community_db()
    # Model artefacts must already be present under MODELS_DIR (provisioned
    # out of band). The pipeline constructor is fail-fast and aborts boot
    # if a required weight / classes.json is missing.
    pipeline = PlantDiseasePipeline(
        detector_path=DETECTOR_PATH,
        classifier_path=CLASSIFIER_PATH,
    )
    logger.info(
        "Pipeline initialised (classifier=%s, %d classes)",
        pipeline.classifier.backend,
        pipeline.classifier.NUM_CLASSES,
    )
    # Daily prune task. Runs in the event loop because the work is
    # I/O-bound (a single DELETE per table); no thread pool needed.
    # Cancelled cleanly on shutdown so the lifespan exit doesn't
    # leak the task.
    prune_task = asyncio.create_task(_prune_loop())
    try:
        yield
    finally:
        logger.info("Server shutting down")
        prune_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prune_task
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
            if removed_fb or removed_cm:
                logger.info(
                    "Prune pass: feedback=%d community=%d", removed_fb, removed_cm,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Prune pass failed (will retry tomorrow): %s", exc)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    # Tightened — the Android client only sends Content-Type. Avoid
    # the wildcard so a XS-Search via a custom header isn't trivially
    # mountable from a malicious origin we forgot to exclude.
    allow_headers=["Content-Type", "Accept"],
)

# Middleware order: outermost runs first.
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
        img.load()  # force actual decoding to surface truncation errors
        # Apply the EXIF orientation tag BEFORE converting to RGB.
        # Phone cameras commonly store sideways/upside-down captures
        # with an orientation flag rather than rotating pixels; PIL drops
        # EXIF on ``convert("RGB")``, so without this the detector and
        # classifier see a rotated leaf and lose accuracy on portrait
        # shots. ``exif_transpose`` is a no-op when no tag is present.
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=400, detail="Image exceeds decompression safety limit") from exc
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc


# ── Endpoints ────────────────────────────────────────────────────────

api_router = APIRouter()

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


@api_router.get("/version")
async def version():
    """Server version and model architecture."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    # Read the int8 ship flag from model_card.json so operators can see at
    # a glance whether the deployed classifier_int8.onnx is genuine INT8
    # or the FP32 ONNX renamed (see MODEL_CARD.md, "Known caveats"). We
    # default conservatively to false if the file is missing or malformed
    # so that callers can never accidentally believe INT8 shipped.
    int8_shipped = False
    int8_artifact: str | None = None
    try:
        with open(MODELS_DIR / "model_card.json", encoding="utf-8") as fh:
            card = json.load(fh)
        int8_section = card.get("int8") or {}
        int8_shipped = bool(int8_section.get("shipped", False))
        artifact = int8_section.get("shipped_artifact")
        if isinstance(artifact, str):
            int8_artifact = artifact
    except (OSError, ValueError):
        pass
    return {
        "version": APP_VERSION,
        "classifier_name": "plantvision",
        "detector_arch": "YOLOv8n (single-class plant_leaf, indoor-trained)",
        # Report the architecture the loaded model actually declares
        # (classes.json -> architecture, surfaced as classifier.architecture)
        # rather than a hardcoded backbone name, so this stays correct
        # across model swaps (the MobileNetV3 student and the frozen
        # DINOv2 ViT-S/14 + head both flow through the same ONNX path).
        "classifier_arch": (
            f"PlantVision {pipeline.classifier.architecture} @ "
            f"{pipeline.classifier.img_size} px / {pipeline.classifier.backend} backend"
        ),
        "stages": 2,
        "focus": "houseplants",
        "int8_shipped": int8_shipped,
        "int8_artifact": int8_artifact,
    }


@api_router.get("/classes")
async def classes():
    """List of known disease classes with bilingual names."""
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    items = []
    for name in pipeline.classifier.CLASS_NAMES:
        info = DISEASES_DATABASE.get(name, {})
        items.append({
            "class": name,
            "name_en": info.get("name_en", name.replace("_", " ").title()),
            "name_ru": info.get("name_ru", name),
            "is_healthy": bool(info.get("is_healthy", False)),
        })
    return {"count": len(items), "classes": items}


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
    """Run PlantNet identify in the inference executor with a tight
    timeout. Returns the compact species block ({class, score,
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
                _get_inference_executor(),
                # identify_species keyword args (organ, lang) are
                # keyword-only — run_in_executor only takes positional
                # args, so we wrap in a lambda. Passing positionally
                # raises TypeError at call time and surfaces as a 500.
                lambda: identify_species(image_bytes, organ="leaf", lang=lang),
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
    image: UploadFile = File(...),
    species: str | None = Query(default=None, max_length=128),
    locale: str = Query(default="en", max_length=16),
):
    """Analyse a plant image for diseases (two-stage pipeline).

    Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
    Returns bilingual (EN/RU) disease diagnosis with treatment advice,
    detected region (with severity score) and the top-k class
    probabilities. ``locale`` (default "en") is forwarded to PlantNet
    so the species ``common_name`` is localised in the response.
    """
    if pipeline is None:
        _record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")

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

    try:
        _validate_image_bytes(contents)
        img = _open_image(contents)
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
    image_sha = hashlib.sha256(contents).hexdigest()
    cache_key = f"{image_sha}:{plantnet_lang}"
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

    loop = asyncio.get_running_loop()
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
            loop.run_in_executor(_get_inference_executor(), pipeline.analyze, img),
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
    # `disease_uncertain:<class>`) and the top-k alternatives card,
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

    response_payload = {
        # Canonical class key the pipeline landed on AFTER all
        # post-processing (weak-healthy reroute, OOD gate, etc.).
        # ``disease_name`` / ``disease_name_ru`` are the localised
        # display strings derived from this key. Clients should echo
        # ``class_name`` back as ``predicted_class`` on /api/feedback
        # — argmax(all_probs) is the PRE-reroute class and would
        # poison per-class accuracy metrics if used instead.
        "class_name": class_name,
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
        "scan_id": _issue_scan_id(),
    }
    # Stash the freshly-built response under its (image_hash,
    # plantnet_lang) key so the NEXT identical upload from the same
    # locale short-circuits at the top of this handler.
    _analyze_cache_put(cache_key, response_payload)
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


# ── Community scan corpus ───────────────────────────────────────────


# Region-string alphabet (country name / code + city / admin area).
# Apostrophe + period + hyphen + space cover the bulk of locality
# names ("Saint-John's", "Plymouth, U.K." trims to "Plymouth"). Commas
# are deliberately excluded — we keep country and locality as separate
# fields so callers never need to embed a comma anywhere.
_REGION_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .'-"
)


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
    if not cleaned or any(ch not in _REGION_ALLOWED for ch in cleaned):
        return ""
    return cleaned


def _normalise_locality(raw: str | None) -> str:
    """Sanitise the sub-country city / admin-area string.

    Returns the cleaned value or "" when the caller didn't send a
    locality (country-only writes are legal — the corpus simply
    skips the second hierarchy level).
    """
    if not raw:
        return ""
    cleaned = raw.strip()[:80]
    if not cleaned or any(ch not in _REGION_ALLOWED for ch in cleaned):
        return ""
    return cleaned


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
    country: str = Field(..., min_length=2, max_length=80)
    region: str = Field(default="", max_length=80)
    disease: str = Field(..., min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    species: str | None = Field(default=None, max_length=128)
    species_score: float | None = Field(default=None, ge=0.0, le=1.0)
    locale: str | None = Field(default=None, max_length=8)

    @field_validator("scan_id", "user_pseudo_id")
    @classmethod
    def _safe_token(cls, v: str) -> str:
        if any(ch not in _SCAN_ID_ALLOWED for ch in v):
            raise ValueError("invalid characters in id; expected [A-Za-z0-9_-]")
        return v

    @field_validator("country")
    @classmethod
    def _safe_country(cls, v: str) -> str:
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
        return _normalise_locality(v)

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
    organ = (organ or "leaf").strip().lower()
    if organ not in ("leaf", "flower", "fruit", "bark", "habit", "auto"):
        organ = "leaf"
    plantnet_lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _get_inference_executor(),
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
        _get_inference_executor(),
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
        _get_inference_executor(),
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
        _get_inference_executor(),
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
        _get_inference_executor(),
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
            _get_inference_executor(),
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

    Privacy note: this endpoint reverses the historical "we never
    store the photo" promise, but only for ``correct=false`` rows
    where the training value is highest. The Android feedback sheet
    discloses this in its copy; if the user wants their image
    purged they can use the existing community delete-for-user path
    (which doesn't currently sweep this directory; a separate
    cleanup endpoint is owed but out of scope here).
    """
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    today_writes = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
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
        _get_inference_executor(),
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
        _get_inference_executor(),
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
        _get_inference_executor(),
        lambda: feedback_attach_image_path(scan_id_clean, filename),
    )
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
    country: str = Form(...),
    region: str = Form(""),
    disease: str = Form(...),
    confidence: float | None = Form(None),
    species: str | None = Form(None),
    species_score: float | None = Form(None),
    locale: str | None = Form(None),
    image: UploadFile | None = File(None),
):
    """Append one community-shared scan to the corpus.

    Hierarchy: ``country`` is the canonical top bucket (required),
    ``region`` is the optional sub-country locality. The image is
    optional — when omitted we still store the row, just without a
    thumbnail.
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
        )
    except Exception as exc:  # noqa: BLE001 — pydantic raises a ValidationError subclass
        logger.info("Community scan validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid scan upload") from exc

    # Enforce the per-IP daily quota BEFORE reading the (up to 10 MB)
    # image body — an over-quota client should be rejected without us
    # ingesting its payload first.
    client_ip = _client_ip(request)
    ip_hash = hash_client_ip(client_ip)
    today_writes = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
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
    try:
        row_id = await asyncio.get_running_loop().run_in_executor(
            _get_inference_executor(),
            lambda: community_record_scan(
                scan_id=payload.scan_id,
                user_pseudo_id=payload.user_pseudo_id,
                country=payload.country,
                region=payload.region,
                disease=payload.disease,
                confidence=payload.confidence,
                species=payload.species,
                species_score=payload.species_score,
                image_bytes=image_bytes,
                locale=payload.locale,
                client_ip=client_ip,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Community scan write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to record scan") from exc

    return JSONResponse(content={"ok": True, "id": row_id})


@api_router.get("/community/nearby")
async def community_scan_nearby(
    country: str | None = None,
    region: str | None = None,
    disease: str | None = None,
    species: str | None = None,
    limit: int = 20,
    user_pseudo_id: str | None = None,
):
    """List the most recent community-shared scans within ``country``.

    ``region`` narrows the result set to a sub-country locality
    (city / admin area) when supplied. ``species`` (scientific name,
    exact match) narrows further so the carousel only shows scans of
    the same plant — without it, "Ficus elastica with leaf spot"
    appears next to "Monstera with leaf spot" and conflates two
    different care routines.

    Fall-through ordering inside ``nearby_scans`` is most-specific
    first: same disease+species+region → same disease+species → …
    → same country (any disease). The carousel always fills up to
    ``limit`` so a thin local bucket still surfaces something.
    """
    country_norm = _normalise_country(country)
    region_norm = _normalise_locality(region) if region else ""
    if not country_norm:
        logger.info("Community nearby missing valid country: %r", country)
        raise HTTPException(status_code=422, detail="Invalid nearby query")
    rows = community_nearby_scans(
        country=country_norm,
        region=region_norm or None,
        disease=(disease or None),
        species=(species or None),
        limit=max(1, min(int(limit), 50)),
        exclude_user_pseudo_id=user_pseudo_id,
    )
    # Exclude the requesting user's own rows from the summary too —
    # otherwise the chip row and the ``last_7d`` count include the
    # scan the user just uploaded and surface phantom diagnoses
    # (e.g. "Healthy Plant · 1" when the only Healthy Plant in the
    # bucket is the user's own).
    summary = community_region_summary(
        country_norm,
        region_norm or None,
        exclude_user_pseudo_id=user_pseudo_id,
    )
    return JSONResponse(content={"items": rows, "summary": summary})


@api_router.get("/community/thumb/{filename}")
async def community_scan_thumb(filename: str):
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
    # FileResponse streams the file off the event loop instead of
    # reading the whole thumbnail into memory inline. Thumbnails are
    # always re-encoded to JPEG by community._save_thumbnail.
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@api_router.delete("/community/user/{user_pseudo_id}")
async def community_scan_delete_user(user_pseudo_id: str):
    """Erase all rows + thumbnails attributed to one install. Used by
    the Android "delete my contributions" affordance.
    """
    if not user_pseudo_id or any(ch not in _SCAN_ID_ALLOWED for ch in user_pseudo_id):
        raise HTTPException(status_code=422, detail="Invalid user id")
    deleted_community = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
        lambda: community_delete_for_user(user_pseudo_id),
    )
    deleted_feedback = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
        lambda: feedback_delete_for_user(user_pseudo_id),
    )
    return JSONResponse(content={
        "ok": True,
        "deleted": deleted_community,
        "deleted_community": deleted_community,
        "deleted_feedback": deleted_feedback,
    })


@api_router.get("/user/export/{user_pseudo_id}")
async def export_user_data(user_pseudo_id: str):
    """GDPR Data Portability: Export all data associated with the user_pseudo_id."""
    if not user_pseudo_id or any(ch not in _SCAN_ID_ALLOWED for ch in user_pseudo_id):
        raise HTTPException(status_code=422, detail="Invalid user id")
    community_data = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
        lambda: community_get_user_data(user_pseudo_id),
    )
    feedback_data = await asyncio.get_running_loop().run_in_executor(
        _get_inference_executor(),
        lambda: feedback_get_user_data(user_pseudo_id),
    )
    return JSONResponse(content={
        "user_pseudo_id": user_pseudo_id,
        "community_scans": community_data,
        "feedback_submissions": feedback_data,
    })


app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=get_str("HOST", "0.0.0.0"),
        port=get_int("PORT", 8000),
        forwarded_allow_ips=get_str("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
