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
import io
import ipaddress
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
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
        compare_progression,
        compare_progression_history,
        generate_care_plan,
        identify_species,
    )
    from .config import get_float, get_int, get_list, get_str
    from .diseases_data import DISEASES_DATABASE, lookup_pet_toxicity
    from .feedback import feedback_counts, record_feedback
    from .feedback import init_db as init_feedback_db
    from .pipeline import PlantDiseasePipeline
except ImportError:
    from ai_services import (
        chat_metrics_snapshot,
        chat_reply,
        compare_progression,
        compare_progression_history,
        generate_care_plan,
        identify_species,
    )
    from config import get_float, get_int, get_list, get_str
    from diseases_data import DISEASES_DATABASE, lookup_pet_toxicity
    from feedback import feedback_counts, record_feedback
    from feedback import init_db as init_feedback_db
    from pipeline import PlantDiseasePipeline

try:
    from . import __version__ as APP_VERSION  # type: ignore
except Exception:
    APP_VERSION = "2.1"

# Limit Pillow decompression to prevent zip-bomb DoS
Image.MAX_IMAGE_PIXELS = 20_000_000  # ~4500×4500

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path(get_str("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

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
MODELS_DIR = Path("models")
DETECTOR_PATH = MODELS_DIR / "detector.pt"       # YOLOv8 weights (auto-prefers detector.onnx if present)
# PlantVision classifier: distilled MobileNetV3-Large student (KD ←
# EfficientNetV2-S teacher). classes.json carries the canonical
# img_size. Production preference order:
# classifier_int8.onnx → classifier.onnx → .pth.
CLASSIFIER_PATH = MODELS_DIR / "classifier.pth"

MAX_FILE_SIZE = get_int("MAX_FILE_SIZE", 10 * 1024 * 1024)  # 10 MB
MAX_IMAGE_DIMENSION = get_int("MAX_IMAGE_DIMENSION", 4096)
INFERENCE_TIMEOUT = get_float("INFERENCE_TIMEOUT", 30.0)
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
TRUSTED_PROXIES = set(get_list("TRUSTED_PROXIES", []))

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
    """Per-IP token-free rate limiter with periodic cleanup."""

    def __init__(self, app, requests_per_second: float = 1.0):
        super().__init__(app)
        self.min_interval = 1.0 / requests_per_second
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def _cleanup_stale(self, now: float) -> None:
        cutoff = now - max(self.min_interval * 2, 60.0)
        stale = [ip for ip, ts in self._last_request.items() if ts < cutoff]
        for ip in stale:
            del self._last_request[ip]

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

        client_ip = _client_ip(request)
        # Use monotonic clock so an NTP step (or daylight-saving jump on
        # a misconfigured host) cannot lock every client out for
        # several minutes by making `now - last` go negative.
        now = time.monotonic()

        with self._lock:
            if now - self._last_cleanup > _RATE_LIMIT_CLEANUP_INTERVAL:
                self._cleanup_stale(now)
                self._last_cleanup = now

            last = self._last_request.get(client_ip, 0.0)
            if now - last < self.min_interval:
                logger.warning("Rate limited IP=%s", client_ip)
                retry_after = max(1, int(math.ceil(self.min_interval - (now - last))))
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={"detail": "Too many requests. Please wait before trying again."},
                )
            self._last_request[client_ip] = now

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
        _record_request()
        start = time.time()
        response: StarletteResponse = await call_next(request)
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
    pipeline = PlantDiseasePipeline(
        detector_path=DETECTOR_PATH,
        classifier_path=CLASSIFIER_PATH,
    )
    logger.info(
        "Pipeline initialised (classifier=%s, %d classes)",
        pipeline.classifier.backend,
        pipeline.classifier.NUM_CLASSES,
    )
    yield
    logger.info("Server shutting down")
    with _executor_lock:
        if _inference_executor is not None:
            # `wait=True` lets in-flight inferences drain. The bound is
            # INFERENCE_TIMEOUT (default 30 s) — anything beyond that is
            # already a stuck request. cancel_futures=True drops queued
            # work that hasn't started yet.
            _inference_executor.shutdown(wait=True, cancel_futures=True)
            _inference_executor = None


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
app.add_middleware(RateLimitMiddleware, requests_per_second=RATE_LIMIT_RPS)
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
    """Decode bytes into a validated RGB PIL image."""
    try:
        img = Image.open(io.BytesIO(contents))
        img.load()  # force actual decoding to surface truncation errors
        return img.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=400, detail="Image exceeds decompression safety limit") from exc
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/api/health")
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


@app.get("/api/version")
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
        with open(MODELS_DIR / "model_card.json", "r", encoding="utf-8") as fh:
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
        "classifier_arch": (
            f"PlantVision MobileNetV3-L (KD ← EfficientNetV2-S) @ "
            f"{pipeline.classifier.img_size} px / {pipeline.classifier.backend} backend"
        ),
        "stages": 2,
        "focus": "houseplants",
        "int8_shipped": int8_shipped,
        "int8_artifact": int8_artifact,
    }


@app.get("/api/classes")
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


@app.get("/api/metrics")
async def metrics():
    """Operational metrics (JSON). Useful for dashboards and jury demos."""
    snap = _snapshot_metrics()
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    return {
        "uptime_seconds": round(uptime, 1),
        **snap,
        "ai_doctor": chat_metrics_snapshot(),
        "feedback": feedback_counts(),
    }


_PROM_LABEL_ESCAPE = str.maketrans({"\\": r"\\", "\"": r"\"", "\n": r"\n"})


def _prom_label(value: str) -> str:
    return value.translate(_PROM_LABEL_ESCAPE)


@app.get("/api/metrics/prometheus")
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


@app.post("/api/analyze")
async def analyze_image(image: UploadFile = File(...), species: str | None = None):
    """Analyse a plant image for diseases (two-stage pipeline).

    Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
    Returns bilingual (EN/RU) disease diagnosis with treatment advice,
    detected region (with severity score) and the top-k class probabilities.
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

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_get_inference_executor(), pipeline.analyze, img),
            timeout=INFERENCE_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        _record_error()
        logger.error("Inference timed out after %.0fs for %s", INFERENCE_TIMEOUT, fname)
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
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
    # UI can render "leaf_spot_fungal OR bacterial_leaf_spot @ 90%"
    # without a second lookup table. ``None`` when calibration data
    # isn't yet available — clients must treat it as optional.
    prediction_set = result.get("prediction_set")
    if prediction_set is not None:
        prediction_set = {
            **prediction_set,
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

    # Pet-toxicity surface — only resolves when the caller passes a
    # `species` hint (PlantNet scientific name or user-typed common
    # name). Without one we can't honestly say a plant is or isn't
    # toxic, so we return ``source="unknown"`` and the client renders
    # an "unconfirmed" copy rather than falsely reassuring.
    toxicity = lookup_pet_toxicity(species) if species else {
        "toxic_to_pets": False, "species_list": [], "source": "unknown",
    }

    return JSONResponse(content={
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
        "elapsed_ms": result["elapsed_ms"],
        "server_version": APP_VERSION,
    })


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


class ProgressionRequest(BaseModel):
    # Back-compat pairwise shape: callers can pass just `current` plus
    # optional `previous`. New callers should populate `history` with a
    # time-ordered list (oldest first) — when present it takes priority
    # over the pairwise fields.
    current: dict | None = None
    previous: dict | None = None
    history: list[dict] | None = None


# Class keys the strict-mode `corrected_class` validator accepts.
# Includes both the active trainable classes (from classes.json) and
# the deferred physiological classes — clients should be allowed to
# point us at conditions the next model release will cover.
_FEEDBACK_ALLOWED_CORRECTIONS: frozenset[str] = frozenset({
    "bacterial_leaf_spot",
    "gray_mold_botrytis",
    "healthy",
    "leaf_spot_fungal",
    "not_a_plant",
    "powdery_mildew",
    "viral_disease",
    # Deferred (post-v2.1) physiological classes — accepted as
    # feedback signals so the retraining loop can prioritise labelling.
    "edema",
    "leaf_scorch_underwatering",
    "nutrient_deficiency",
    "sunburn_or_scorch",
    # Catch-all the client can send when none of the above match.
    "other",
})

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


@app.post("/api/identify")
async def identify_endpoint(image: UploadFile = File(...), organ: str = "leaf"):
    """PlantNet-backed plant species identification.

    Returns up to 5 candidate species with confidence scores. When the
    PlantNet API key is missing we still respond 200 OK with
    ``available: false`` so the client can render a graceful state
    instead of an error toast. Cached server-side by SHA-256(image) +
    organ to keep the daily PlantNet quota intact.
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
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _get_inference_executor(), identify_species, contents, organ,
    )
    return JSONResponse(content={**result, "organ": organ})


@app.post("/api/chat")
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
    if not result.get("available"):
        # Abuse logging: cap-rejection from a specific IP is the
        # signature pattern for a runaway client. Log at WARNING so
        # ops dashboards can alert on it.
        if result.get("reason") in ("session_turn_cap", "session_token_cap"):
            logger.warning(
                "AI Doctor budget cap hit  ip=%s session=%s reason=%s",
                client_ip, payload.session_id, result["reason"],
            )
    return JSONResponse(content=result)


@app.post("/api/care-plan")
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


@app.post("/api/progression")
async def progression_endpoint(payload: ProgressionRequest):
    """Disease-progression analysis. Accepts two shapes:

      * **Historical**: ``history=[scan1, scan2, ...]`` (oldest first).
        Returns a 5-point severity sparkline plus a trend slope across
        the full history. This is the new path for the Android plant-
        detail screen.
      * **Pairwise (legacy)**: ``current`` + optional ``previous``.
        Returns the same trend / delta_* fields the v2.0 client
        expects. Kept for backward compatibility — older clients still
        in the wild pre-date the historical endpoint.

    When ``history`` is non-empty it takes precedence and the response
    additionally carries the sparkline. When only the pairwise fields
    are supplied the response keeps the v2.0 shape so older clients
    never see an unexpected key, but new clients can rely on the
    sparkline + history_length fields.
    """
    if payload.history is not None:
        return JSONResponse(content=compare_progression_history(payload.history))
    # Pairwise back-compat path.
    if payload.current is None:
        raise HTTPException(status_code=400, detail="current or history is required")
    return JSONResponse(
        content=compare_progression(
            current=payload.current, previous=payload.previous,
        )
    )


@app.post("/api/feedback")
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
    # Pydantic v2 has already normalised these — corrected_class is
    # constrained to the known class set, reason to the known reason
    # set, scan_id / user_pseudo_id to the URL-safe alphabet, etc.
    note = (payload.note or "").strip() or None
    top3_pairs = (
        [(entry["class"], entry["prob"]) for entry in payload.top3]
        if payload.top3 else None
    )

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=get_str("HOST", "0.0.0.0"),
        port=get_int("PORT", 8000),
        forwarded_allow_ips=get_str("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
