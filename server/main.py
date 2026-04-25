"""
PlantDiseases Server — FastAPI backend with the PlantScope v3.x two-stage
houseplant disease pipeline.

Stage 1:  YOLOv8n            — locate leaves, flag diseased regions
Stage 2:  EfficientNetV2-S   — classify the primary disease bbox
"""

import asyncio
import io
import ipaddress
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from diseases_data import DISEASES_DATABASE
from pipeline import PlantDiseasePipeline

try:
    from . import __version__ as APP_VERSION  # type: ignore
except Exception:
    APP_VERSION = "3.0.0"

# Limit Pillow decompression to prevent zip-bomb DoS
Image.MAX_IMAGE_PIXELS = 20_000_000  # ~4500×4500

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
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
DETECTOR_PATH = MODELS_DIR / "detector.pt"       # YOLOv8 weights
CLASSIFIER_PATH = MODELS_DIR / "classifier.pth"  # EfficientNetV2-S weights

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 10 * 1024 * 1024))  # 10 MB
MAX_IMAGE_DIMENSION = int(os.getenv("MAX_IMAGE_DIMENSION", 4096))
INFERENCE_TIMEOUT = float(os.getenv("INFERENCE_TIMEOUT", 30.0))
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
IMAGE_MAGIC = (
    b"\xff\xd8\xff",            # JPEG
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"RIFF",                    # WebP (RIFF....WEBP)
    b"BM",                      # BMP
)

RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", 1.0))
TRUSTED_PROXIES = {
    p.strip() for p in os.getenv("TRUSTED_PROXIES", "").split(",") if p.strip()
}

INFER_WORKERS = int(os.getenv("INFER_WORKERS", 2))
_inference_executor: ThreadPoolExecutor | None = None


def _get_inference_executor() -> ThreadPoolExecutor:
    """Lazily (re)create the inference executor so test harnesses that cycle
    the FastAPI lifespan can reuse it after a previous shutdown."""
    global _inference_executor
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
        self._last_cleanup = time.time()

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
        now = time.time()

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
    global pipeline, SERVER_START_TIME, _inference_executor
    SERVER_START_TIME = time.time()
    _inference_executor = _get_inference_executor()
    try:
        pipeline = PlantDiseasePipeline(
            detector_path=DETECTOR_PATH,
            classifier_path=CLASSIFIER_PATH,
        )
        logger.info("Pipeline initialised (mode=%s)", pipeline.mode)
    except Exception as e:
        logger.error("Failed to initialise pipeline: %s", e)
        pipeline = PlantDiseasePipeline()
    yield
    logger.info("Server shutting down")
    if _inference_executor is not None:
        _inference_executor.shutdown(wait=False)
        _inference_executor = None


app = FastAPI(
    title="PlantDiseases API",
    description="AI-powered plant disease detection — two-stage pipeline",
    version=APP_VERSION,
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────
_cors_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
ALLOWED_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if ALLOWED_ORIGINS == ["*"]:
    logger.warning("CORS_ORIGINS=* is insecure for production; restrict it in deployment.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Middleware order: outermost runs first.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_second=RATE_LIMIT_RPS)


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
    if not any(head.startswith(sig) for sig in IMAGE_MAGIC):
        # Extra check for WebP (RIFF....WEBP)
        if not (head.startswith(b"RIFF") and len(contents) >= 12 and contents[8:12] == b"WEBP"):
            raise HTTPException(
                status_code=400,
                detail="Unrecognised image format. Allowed: JPEG, PNG, WebP, BMP",
            )


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
    """Health check — server status, model state, pipeline mode, uptime, request count."""
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    return {
        "status": "ok",
        "version": APP_VERSION,
        "pipeline_mode": pipeline.mode if pipeline else "unknown",
        "detector_loaded": pipeline.detector.is_loaded if pipeline else False,
        "classifier_loaded": pipeline.classifier.is_loaded if pipeline else False,
        "num_classes": len(pipeline.classifier.CLASS_NAMES) if pipeline else 0,
        "uptime_seconds": round(uptime, 1),
        "total_requests": _snapshot_metrics()["total_requests"],
    }


@app.get("/api/version")
async def version():
    """Server version and model architecture."""
    return {
        "version": APP_VERSION,
        "pipeline_mode": pipeline.mode if pipeline else "unknown",
        "detector_arch": "YOLOv8n",
        "classifier_arch": "EfficientNetV2-S",
        "stages": 2,
        "focus": "houseplants",
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
        "pipeline_mode": pipeline.mode if pipeline else "unknown",
    }


_PROM_LABEL_ESCAPE = str.maketrans({"\\": r"\\", "\"": r"\"", "\n": r"\n"})


def _prom_label(value: str) -> str:
    return value.translate(_PROM_LABEL_ESCAPE)


@app.get("/api/metrics/prometheus")
async def metrics_prometheus():
    """Prometheus text exposition (v0.0.4) of the same counters as /api/metrics."""
    snap = _snapshot_metrics()
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    mode = pipeline.mode if pipeline else "unknown"
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
        "# HELP plantdiseases_pipeline_mode_info Pipeline mode and server version (value is always 1).",
        "# TYPE plantdiseases_pipeline_mode_info gauge",
        f'plantdiseases_pipeline_mode_info{{mode="{_prom_label(mode)}",version="{version}"}} 1',
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
async def analyze_image(image: UploadFile = File(...)):
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

    contents = await image.read()
    try:
        _validate_image_bytes(contents)
    except HTTPException:
        _record_error()
        raise

    try:
        img = _open_image(contents)
    except HTTPException:
        _record_error()
        raise

    img_w, img_h = img.size
    if img_w > MAX_IMAGE_DIMENSION or img_h > MAX_IMAGE_DIMENSION:
        _record_error()
        logger.warning("Rejected oversized image: %dx%d from %s", img_w, img_h, image.filename)
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
        logger.error("Inference timed out after %.0fs for %s", INFERENCE_TIMEOUT, image.filename)
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
    disease_info = DISEASES_DATABASE.get(class_name, DISEASES_DATABASE["unknown"])

    elapsed = time.time() - start_time
    _record_analysis(class_name, elapsed * 1000)

    logger.info(
        "Analysis complete: class=%s confidence=%.3f time=%.2fs file=%s mode=%s",
        class_name, confidence, elapsed, image.filename, result["pipeline_mode"],
    )

    rounded_probs = {k: round(v, 4) for k, v in all_probs.items()}

    # Compute Shannon-entropy uncertainty in [0, 1]; higher = more uncertain.
    if rounded_probs:
        vals = [p for p in rounded_probs.values() if p > 0]
        entropy = -sum(p * math.log(p) for p in vals)
        max_entropy = math.log(len(rounded_probs))
        uncertainty = round(entropy / max_entropy, 4) if max_entropy > 0 else 0.0
    else:
        uncertainty = 0.0

    detection = result["detection"]

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
        "is_healthy": disease_info.get("is_healthy", False),
        "detection": detection,
        "uncertainty": uncertainty,
        "all_probs": rounded_probs,
        "pipeline_mode": result["pipeline_mode"],
        "elapsed_ms": result["elapsed_ms"],
        "server_version": APP_VERSION,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        proxy_headers=True,
    )
