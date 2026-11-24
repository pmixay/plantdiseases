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

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
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
        account_auth,
        account_entitlements,
        account_roles,
        ai_services,
        app_version,
        beta_runtime,
        community,
        community_achievements,
        community_deletion,
        community_feeds,  # noqa: F401  (routes reach it as m.community_feeds)
        community_forum,
        community_labels,
        community_safety,  # noqa: F401  (routes reach it as m.community_safety)
        community_views,  # noqa: F401  (routes reach it as m.community_views)
        config,
        deps,
        feedback_labels,
        plant_photos,
        popular_bundle,
        popular_plants,
        routes_assistant,
        routes_community,
        routes_content,
        routes_diagnosis,
        routes_forum,
        routes_internal,
        routes_meta,
        routes_moderation,
        runtime_paths,
        species_info,
        synthetic_pipeline,
        telemetry,
        training_store,
    )
    from . import (
        pipeline as pipeline_module,
    )
except ImportError:
    import account_auth
    import account_entitlements
    import account_roles
    import ai_services
    import app_version
    import beta_runtime
    import community
    import community_achievements
    import community_deletion
    import community_feeds  # noqa: F401
    import community_forum
    import community_labels
    import community_safety  # noqa: F401
    import community_views  # noqa: F401
    import config
    import deps
    import feedback_labels
    import pipeline as pipeline_module
    import plant_photos
    import popular_bundle
    import popular_plants
    import routes_assistant
    import routes_community
    import routes_content
    import routes_diagnosis
    import routes_forum
    import routes_internal
    import routes_meta
    import routes_moderation
    import runtime_paths
    import species_info
    import synthetic_pipeline
    import telemetry
    import training_store


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
LOG_DIR = Path(config.get_str("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

USE_STRUCTURED_LOGS = config.get("STRUCTURED_LOGS", False, cast=bool)
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
ENVIRONMENT = config.get_str("ENVIRONMENT", "").strip().lower()
ACCOUNT_BETA_SYNTHETIC_PIPELINE = beta_runtime.allow_synthetic_pipeline(
    environment=ENVIRONMENT,
    requested=config.get_bool("ACCOUNT_BETA_SYNTHETIC_PIPELINE", False),
    synthetic_staging_acknowledged=config.get_bool(
        "ACCOUNT_BETA_SYNTHETIC_DATA_ONLY",
        False,
    ),
)
APP_ROOT_PATH = runtime_paths.normalize_root_path(config.get_str("APP_ROOT_PATH", ""))
POPULAR_WARM_ENABLED = config.get_bool("POPULAR_WARM_ENABLED", True)
ACCOUNT_FEATURE_EXPECTED = config.get_bool(
    "ACCOUNT_FEATURE_EXPECTED",
    bool(
        config.get_str("ACCOUNT_API_BASE_URL", "").strip()
        or config.get_str("ACCOUNT_ISSUER", "").strip()
    ),
)
account_authenticator = account_auth.build_account_authenticator(expected=ACCOUNT_FEATURE_EXPECTED)
community_bearer = HTTPBearer(auto_error=False)
# ``Depends`` target for routes that only need the signed-in account (see deps.py).
account_dependency = deps.account_required(sys.modules[__name__])

MODELS_DIR = Path(config.get_str("MODELS_DIR", "models"))
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
FRONTIER_ENABLED = config.get_bool("FRONTIER_ENABLED", False)
# Frontier lives in its own complete manifest-bound directory so no detector,
# classifier, taxonomy, or calibration file can collide with gamma's bundle.
FRONTIER_MODELS_DIR = Path(config.get_str("FRONTIER_MODELS_DIR", str(MODELS_DIR / "frontier")))
FRONTIER_DETECTOR_PATH = FRONTIER_MODELS_DIR / "detector.onnx"
FRONTIER_CLASSIFIER_PATH = FRONTIER_MODELS_DIR / "classifier.onnx"

# ── De-identified training-capture trust knobs ───────────────────────
# Confident weak labels become ``auto_yes``; uncertainty remains ``pending``
# for offline-only review. No serving-side human/admin queue exists.
TRAINING_TRUST_CONFORMAL = config.get_bool("TRAINING_TRUST_CONFORMAL", True)
TRAINING_CONF_HIGH = config.get_float("TRAINING_CONF_HIGH", 0.85)
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
TRAINING_CAPTURE_ENABLED = config.get_bool("TRAINING_CAPTURE_ENABLED", False)
TRAINING_CONSENT_VERSION = config.get_str(
    "TRAINING_CONSENT_VERSION", "model-improvement-v1"
).strip()
TRAINING_RETENTION_DAYS = config.get_int("TRAINING_RETENTION_DAYS", 90)
TRAINING_MAX_IMAGES = config.get_int("TRAINING_MAX_IMAGES", 20000)
# Value caps: confident scans of the abundant easy class (healthy) are sampled
# at this rate so a flood of confident-healthy photos can't drown the disease
# corpus; per-class daily cap bounds disk regardless of class.
TRAINING_HEALTHY_KEEP_RATE = config.get_float("TRAINING_HEALTHY_KEEP_RATE", 0.1)
TRAINING_DAILY_CAP_PER_CLASS = config.get_int("TRAINING_DAILY_CAP_PER_CLASS", 50)

# ── Guide example photos ─────────────────────────────────────────────
# Disease/pest example photos used by the app's guide + after-scan strip.
# They live with the SERVER (server/static/guide/<key>.png) rather than the
# APK so the app stays small; the client downloads + disk-caches them, so
# once seen they remain available offline. Path is resolved relative to this
# module so it works whether uvicorn runs from server/ or the repo root.
GUIDE_STATIC_DIR = Path(config.get_str(
    "GUIDE_STATIC_DIR", str(Path(__file__).resolve().parent / "static" / "guide")))

# ── Aqua on-device model hosting ─────────────────────────────────────
# The Aqua TFLite student is served to the app so it can download + cache it
# (and pick up updates) instead of bundling it in the APK — same rationale as
# the guide photos. The artefact is provisioned out of band (never committed);
# when absent the manifest reports available:false and the app falls back to
# the server model. Publish ``aqua/aqua_classifier.tflite`` plus
# ``aqua/classes.json`` from the same verified combined bundle here. The
# endpoint binds both file digests and the combined-bundle identity.
AQUA_STATIC_DIR = Path(config.get_str(
    "AQUA_STATIC_DIR", str(Path(__file__).resolve().parent / "static" / "aqua")))

# Model artefacts are NOT committed or auto-downloaded. Extract one complete
# notebook bundle tree into MODELS_DIR. Boot is fail-fast on a missing/invalid
# manifest, changed bytes, mixed runs, or a serving-contract mismatch.
MAX_FILE_SIZE = config.get_int("MAX_FILE_SIZE", 10 * 1024 * 1024)  # 10 MB
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
MAX_REQUEST_BYTES = config.get_int("MAX_REQUEST_BYTES", MAX_FILE_SIZE + 2 * 1024 * 1024)
MAX_IMAGE_DIMENSION = config.get_int("MAX_IMAGE_DIMENSION", 4096)
PLANTNET_IMAGE_MAX_DIMENSION = config.get_int("PLANTNET_IMAGE_MAX_DIMENSION", 1024)
# Reject degenerate thumbnails. A tiny upload (e.g. 8x8) carries no
# diagnostic signal but the blur gate passes it (upscaled noise has
# high Laplacian variance) and the detector/classifier then emit a
# confident — and meaningless — verdict (before the severity fix, an 8x8
# noise image scored viral_disease @0.72 with the old fabricated "high"
# severity). Anything below
# this on the short side is short-circuited to a clean 400 so the
# client asks the user to retake instead of trusting a fake diagnosis.
MIN_IMAGE_DIMENSION = config.get_int("MIN_IMAGE_DIMENSION", 48)
INFERENCE_TIMEOUT = config.get_float("INFERENCE_TIMEOUT", 30.0)

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
ANALYZE_CACHE_TTL_SECONDS = config.get_int("ANALYZE_CACHE_TTL_SECONDS", 24 * 3600)
ANALYZE_CACHE_MAX_ENTRIES = config.get_int("ANALYZE_CACHE_MAX_ENTRIES", 512)
_analyze_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_analyze_cache_lock = threading.Lock()


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


RATE_LIMIT_RPS = config.get_float("RATE_LIMIT_RPS", 2.0)
# Burst capacity on top of the steady-state rate. One user SCAN fans out into
# many requests — analyze + identify + capabilities + aqua/manifest + care-plan
# + species-info + community/nearby + community/by-species — so a
# burst of 5 was far too small: a single scan overflowed the bucket, each
# overflow was a 429, and 5 of those tripped the abuse auto-ban below (the
# "Server error: 403 for no reason" users saw). 20 absorbs a couple of scans'
# fan-out back-to-back before the steady rate takes over.
RATE_LIMIT_BURST = config.get_float("RATE_LIMIT_BURST", 20.0)
TRUSTED_PROXIES = set(config.get_list("TRUSTED_PROXIES", []))

# API Keys configuration
REQUIRE_API_KEY = config.get("REQUIRE_API_KEY", False, cast=bool)
api_keys_raw = config.get("API_KEYS", {})
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
    tuple[int, str, str, str], account_entitlements.EntitlementDecision
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
VIOLATION_THRESHOLD = config.get_int("VIOLATION_THRESHOLD", 40)
VIOLATION_WINDOW = config.get_float("VIOLATION_WINDOW", 60.0)
BAN_DURATION = config.get_float("BAN_DURATION", 300.0)

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
) -> account_entitlements.EntitlementDecision:
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
            return account_entitlements.EntitlementDecision(
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
            decision = account_entitlements.EntitlementDecision(
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
            decision = account_entitlements.EntitlementDecision(
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


async def _require_account(request: Request) -> account_auth.AccountPrincipal:
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
    except account_auth.AccountAuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="auth_required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except account_auth.AccountAuthenticationUnavailable:
        raise HTTPException(status_code=503, detail="account_auth_unavailable") from None


async def _optional_account(request: Request) -> account_auth.AccountPrincipal | None:
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
    "gamma_analysis": max(0, config.get_int("GUEST_GAMMA_ANALYSIS_DAILY_QUOTA", 5)),
    "frontier_analysis": max(0, config.get_int("GUEST_FRONTIER_ANALYSIS_DAILY_QUOTA", 1)),
    "plant_identification": max(0, config.get_int("GUEST_IDENTIFY_DAILY_QUOTA", 10)),
    "ai_doctor": max(0, config.get_int("GUEST_AI_DOCTOR_DAILY_QUOTA", 5)),
    "care_plan": max(0, config.get_int("GUEST_CARE_PLAN_DAILY_QUOTA", 10)),
}
_guest_feature_lock = threading.Lock()
_guest_feature_counts: dict[tuple[int, str, str], int] = {}
_guest_feature_receipts: dict[tuple[int, str, str, str], account_entitlements.EntitlementDecision] = {}
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
) -> account_entitlements.EntitlementDecision:
    limit = _GUEST_FEATURE_LIMITS[feature]
    day = int(time.time()) // 86_400 if day is None else day
    count_key = (day, feature, address)
    receipt_key = (day, feature, address, request_id)
    reset_at = _quota_reset_at(day)
    with _guest_feature_lock:
        previous = _guest_feature_receipts.get(receipt_key)
        if previous is not None:
            return account_entitlements.EntitlementDecision(
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
            decision = account_entitlements.EntitlementDecision(
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
            decision = account_entitlements.EntitlementDecision(
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


def _released_decision(previous: account_entitlements.EntitlementDecision) -> account_entitlements.EntitlementDecision:
    """Represent an exact-once local release without enabling UUID reuse."""

    return account_entitlements.EntitlementDecision(
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

    client = account_entitlements.account_entitlement_client
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
    except account_entitlements.EntitlementUnavailable:
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
) -> account_entitlements.EntitlementDecision:
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
        client = account_entitlements.account_entitlement_client
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
        except account_entitlements.EntitlementUnavailable:
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

    if not ai_services.plantnet_configured():
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
ANALYZE_DAILY_QUOTA = config.get_int("ANALYZE_DAILY_QUOTA", 300)
IDENTIFY_DAILY_QUOTA = config.get_int("IDENTIFY_DAILY_QUOTA", 150)
# PlantNet calls per IP, shared by the /api/analyze fan-out and /api/identify.
# Past it a scan still gets its diagnosis, without the species block.
PLANTNET_DAILY_QUOTA_PER_IP = config.get_int("PLANTNET_DAILY_QUOTA_PER_IP", 100)
# AI Plant Doctor turns per IP. The per-session caps in ai_services reset
# with every new client-chosen session_id; this one does not.
CHAT_DAILY_QUOTA = config.get_int("CHAT_DAILY_QUOTA", 100)
# LLM-polished care plans per IP. Past it the rules-based plan is served.
CARE_PLAN_DAILY_QUOTA = config.get_int("CARE_PLAN_DAILY_QUOTA", 200)
# /api/species-diseases + /api/species-profile together, per IP. Their caches
# key on client-chosen names, so every new string is a billable miss.
SPECIES_AI_DAILY_QUOTA = config.get_int("SPECIES_AI_DAILY_QUOTA", 400)


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


# Client error/problem telemetry ingestion. Errors-only, opt-out here; the
# cap + retention live in telemetry.py.
TELEMETRY_ENABLED = config.get_bool("TELEMETRY_ENABLED", True)
TELEMETRY_CONSENT_VERSION = config.get_str(
    "TELEMETRY_CONSENT_VERSION", "telemetry-errors-v1"
).strip()
TELEMETRY_DAILY_CAP_PER_IP = config.get_int("TELEMETRY_DAILY_CAP_PER_IP", 200)
TELEMETRY_RETENTION_DAYS = config.get_int("TELEMETRY_RETENTION_DAYS", 30)
# Retention windows (days). Daily background task in the FastAPI
# lifespan drops rows past this age.
COMMUNITY_RETENTION_DAYS = config.get_int("COMMUNITY_RETENTION_DAYS", 90)
PRUNE_INTERVAL_SECONDS = config.get_int("PRUNE_INTERVAL_SECONDS", 24 * 60 * 60)

# Server-issued community publication tokens are separate from feedback
# receipts and expire independently.
COMMUNITY_ANALYSIS_TOKEN_TTL_DAYS = config.get_int(
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
INFER_WORKERS = config.get_int("INFER_WORKERS", 1)
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
NET_WORKERS = config.get_int("NET_WORKERS", 16)
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
INFER_MAX_INFLIGHT = config.get_int("INFER_MAX_INFLIGHT", 4 * max(1, INFER_WORKERS))
INFER_MAX_INFLIGHT_PER_IP = config.get_int("INFER_MAX_INFLIGHT_PER_IP", 2)
INFER_BUSY_RETRY_AFTER_SECONDS = config.get_int("INFER_BUSY_RETRY_AFTER_SECONDS", 3)

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
METRICS_SNAPSHOT_TTL_SECONDS = max(0.0, config.get_float("METRICS_SNAPSHOT_TTL_SECONDS", 5.0))
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
    community_stats = await loop.run_in_executor(None, community.stats_snapshot)
    _metrics_store_snapshot = (time.monotonic(), training, community_stats)
    return training, community_stats


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


pipeline: pipeline_module.PlantDiseasePipeline | synthetic_pipeline.SyntheticPipeline | None = None
# Optional second pipeline for the frontier family. Stays ``None`` unless
# FRONTIER_ENABLED is set AND the frontier artefacts load cleanly at boot.
pipeline_frontier: pipeline_module.PlantDiseasePipeline | None = None


def _family_available(family: str) -> bool:
    """Whether the server can answer a request for this family.

    ``gamma`` is available whenever the base pipeline loaded. ``aqua`` is an
    on-device family but the server can stand in with gamma, so it tracks
    gamma's availability. ``frontier`` requires its own pipeline to be loaded.
    """
    if family == "frontier":
        return pipeline_frontier is not None
    return pipeline is not None


# ── Rate limiter cleanup interval (seconds) ─────────────────────────
_RATE_LIMIT_CLEANUP_INTERVAL = 300


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
    community.init_db()
    telemetry.init_db()
    # One complete notebook-produced bundle must already be present under
    # MODELS_DIR. The constructor verifies its manifest, hashes, lineage and
    # serving contract before loading the exact selected graphs.
    if ACCOUNT_BETA_SYNTHETIC_PIPELINE:
        pipeline = synthetic_pipeline.SyntheticPipeline()
        logger.warning("Synthetic test-only inference pipeline enabled")
    else:
        pipeline = pipeline_module.PlantDiseasePipeline(
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
            pipeline_frontier = pipeline_module.PlantDiseasePipeline(
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
                None, community_deletion.prune_older_than, COMMUNITY_RETENTION_DAYS,
            )
            cleanup = await asyncio.get_running_loop().run_in_executor(
                None, community_deletion.process_cleanup_jobs,
            )
            forum = await asyncio.get_running_loop().run_in_executor(
                _get_net_executor(), _forum_maintenance,
            )
            removed_tm = await asyncio.get_running_loop().run_in_executor(
                None, telemetry.prune_older_than, TELEMETRY_RETENTION_DAYS,
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
    version=app_version.APP_VERSION,
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
ALLOWED_ORIGINS = config.get_list(
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


# ── Consent-gated, de-identified model-improvement capture ───────────


# ── Endpoints ────────────────────────────────────────────────────────

api_router = APIRouter(prefix="/api")


# ── Smart APIs ──────────────────────────────────────────────────────


# ── Community scan corpus ───────────────────────────────────────────


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
        if any(ch not in feedback_labels.SCAN_ID_ALLOWED for ch in value):
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
        if cleaned not in feedback_labels.FEEDBACK_ALLOWED_CORRECTIONS:
            raise ValueError(
                f"corrected_class must be one of "
                f"{sorted(feedback_labels.FEEDBACK_ALLOWED_CORRECTIONS)}; got {v!r}"
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
        if cleaned not in feedback_labels.FEEDBACK_ALLOWED_REASONS:
            raise ValueError(
                f"reason must be one of {sorted(feedback_labels.FEEDBACK_ALLOWED_REASONS)}; got {v!r}"
            )
        return cleaned

    @model_validator(mode="after")
    def _consistent_correction(self) -> "FeedbackRequest":
        if self.correct and self.corrected_class is not None:
            raise ValueError("corrected_class is only valid for negative feedback")
        return self


# ── Community scan corpus ───────────────────────────────────────────


# Same content-type and magic-byte set the analyze endpoint accepts. We rely on
# Content-Type for the multipart form parse, then re-validate the
# bytes; an attacker can lie about Content-Type but the magic bytes
# don't lie.
_COMMUNITY_ALLOWED_TYPES = set(ALLOWED_CONTENT_TYPES)


# ``popular_bundle.popular_cache_key(...)`` -> (expiry_epoch, payload); see
# popular_bundle.py for why the TTL is short.
_popular_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_popular_lock = threading.Lock()
_POPULAR_CACHE_MAX = 64


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
    for p in popular_plants.popular_for(None):
        if p.scientific in seen:
            continue
        seen.add(p.scientific)
        for lang in ("en", "ru"):
            info = None
            with contextlib.suppress(Exception):
                info = await loop.run_in_executor(
                    _get_net_executor(), lambda s=p.scientific, n=lang: species_info.fetch_species_info(s, n),
                )
            # Pre-cache the lead photo bytes too, so the very first carousel
            # request serves images instantly from our disk cache.
            url = (info or {}).get("image_url")
            if url and url not in warmed_photos:
                warmed_photos.add(url)
                with contextlib.suppress(Exception):
                    await loop.run_in_executor(
                        _get_net_executor(), lambda u=url: plant_photos.get_or_fetch(u),
                    )
    for locale in ("en", "ru"):
        with contextlib.suppress(Exception):
            bundle = await loop.run_in_executor(
                _get_net_executor(), lambda n=locale: popular_bundle.build_popular_bundle(None, None, None, None, n),
            )
            key = popular_bundle.popular_cache_key(None, None, None, locale)
            with _popular_lock:
                _popular_cache[key] = (time.time() + popular_bundle.popular_ttl(bundle), bundle)
                _popular_cache.move_to_end(key)


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
                _get_net_executor(), lambda: popular_bundle.build_popular_bundle(country, city, lat, lon, locale),
            )
            with _popular_lock:
                _popular_cache[key] = (time.time() + popular_bundle.popular_ttl(bundle), bundle)
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
        host=config.get_str("HOST", "0.0.0.0"),  # nosec B104
        port=config.get_int("PORT", 8000),
        forwarded_allow_ips=config.get_str("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
