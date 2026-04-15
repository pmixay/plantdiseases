"""
PlantDiseases Server — FastAPI backend with two-stage CNN pipeline.

Stage 1:  MobileNetV3-Small  — detect diseased region (Grad-CAM)
Stage 2:  EfficientNet-B0    — classify the specific disease

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import io
import os
import time
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from pipeline import PlantDiseasePipeline
from diseases_data import DISEASES_DATABASE

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

# Rotating file handler — 5 MB per file, keep 5 backups
file_handler = RotatingFileHandler(
    LOG_DIR / "server.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, file_handler],
)
logger = logging.getLogger("plantdiseases")

# ── Configuration ────────────────────────────────────────────────────
MODELS_DIR = Path("models")
DETECTOR_PATH = MODELS_DIR / "detector.pth"
CLASSIFIER_PATH = MODELS_DIR / "classifier.pth"

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}

# ── Server metrics ──────────────────────────────────────────────────
SERVER_START_TIME: float = 0.0
TOTAL_REQUESTS: int = 0

pipeline: PlantDiseasePipeline | None = None


# ── Rate limiting middleware ────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter: 1 request per second per IP."""

    def __init__(self, app, requests_per_second: float = 1.0):
        super().__init__(app)
        self.min_interval = 1.0 / requests_per_second
        self._last_request: dict[str, float] = defaultdict(float)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path == "/api/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        last = self._last_request[client_ip]

        if now - last < self.min_interval:
            logger.warning("Rate limited IP=%s", client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please wait before trying again."},
            )

        self._last_request[client_ip] = now
        return await call_next(request)


# ── Request logging middleware ──────────────────────────────────────
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        global TOTAL_REQUESTS
        TOTAL_REQUESTS += 1

        start = time.time()
        response: StarletteResponse = await call_next(request)
        elapsed = time.time() - start

        logger.info(
            "REQUEST %s %s -> %d (%.3fs) IP=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
            request.client.host if request.client else "unknown",
        )
        return response


# ── Lifecycle ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, SERVER_START_TIME
    SERVER_START_TIME = time.time()
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


app = FastAPI(
    title="PlantDiseases API",
    description="AI-powered plant disease detection — two-stage pipeline",
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS — restrict origins for production ──────────────────────────
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Middleware stack (order matters: outermost runs first) ───────────
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_second=1.0)


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check — server status, model state, pipeline mode, uptime, request count."""
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME else 0
    return {
        "status": "ok",
        "version": "2.0.0",
        "pipeline_mode": pipeline.mode if pipeline else "unknown",
        "detector_loaded": pipeline.detector.is_loaded if pipeline else False,
        "classifier_loaded": pipeline.classifier.is_loaded if pipeline else False,
        "num_classes": len(pipeline.classifier.CLASS_NAMES) if pipeline else 0,
        "uptime_seconds": round(uptime, 1),
        "total_requests": TOTAL_REQUESTS,
    }


@app.post("/api/analyze")
async def analyze_image(image: UploadFile = File(...)):
    """Analyse a plant image for diseases (two-stage pipeline).

    Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
    Returns bilingual (EN/RU) disease diagnosis with treatment advice
    and the detected region coordinates.
    """
    start_time = time.time()

    # ── Input validation ─────────────────────────────────────────
    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        logger.warning("Rejected file with content_type=%s", image.content_type)
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Allowed: JPEG, PNG, WebP, BMP",
        )

    contents = await image.read()
    if len(contents) > MAX_FILE_SIZE:
        logger.warning("Rejected oversized file: %d bytes", len(contents))
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB",
        )
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file received")

    # ── Image decoding ───────────────────────────────────────────
    try:
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception:
        logger.error("Failed to decode image from %s", image.filename)
        raise HTTPException(status_code=400, detail="Cannot read image file")

    # ── Pipeline ─────────────────────────────────────────────────
    result = pipeline.analyze(img)

    class_name = result["class_name"]
    confidence = result["confidence"]
    disease_info = DISEASES_DATABASE.get(class_name, DISEASES_DATABASE["unknown"])

    elapsed = time.time() - start_time
    logger.info(
        "Analysis complete: class=%s confidence=%.3f time=%.2fs file=%s mode=%s",
        class_name, confidence, elapsed, image.filename, result["pipeline_mode"],
    )

    return JSONResponse(content={
        # ── backward-compatible fields ──
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
        # ── new pipeline fields ──
        "detection": result["detection"],
        "pipeline_mode": result["pipeline_mode"],
        "elapsed_ms": result["elapsed_ms"],
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
