"""
PlantDiseases Server — FastAPI backend with two-stage CNN pipeline.

Stage 1:  MobileNetV3-Small  — detect diseased region (Grad-CAM)
Stage 2:  EfficientNet-B0    — classify the specific disease

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import io
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image

from pipeline import PlantDiseasePipeline
from diseases_data import DISEASES_DATABASE

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("plantdiseases")

# ── Configuration ────────────────────────────────────────────────────
MODELS_DIR = Path("models")
DETECTOR_PATH = MODELS_DIR / "detector.pth"
CLASSIFIER_PATH = MODELS_DIR / "classifier.pth"

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}

pipeline: PlantDiseasePipeline | None = None


# ── Lifecycle ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check — server status, model state, pipeline mode."""
    return {
        "status": "ok",
        "version": "2.0.0",
        "pipeline_mode": pipeline.mode if pipeline else "unknown",
        "detector_loaded": pipeline.detector.is_loaded if pipeline else False,
        "classifier_loaded": pipeline.classifier.is_loaded if pipeline else False,
        "num_classes": len(pipeline.classifier.CLASS_NAMES) if pipeline else 0,
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
