"""
PlantDiseases Server — FastAPI backend with CNN model for plant disease detection.

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import io
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image

from model import PlantDiseaseModel
from diseases_data import DISEASES_DATABASE

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("plantdiseases")

# ── Configuration ────────────────────────────────────────────────────
MODEL_PATH = Path("models/plant_disease_model.h5")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}

model: PlantDiseaseModel | None = None


# ── Lifecycle (replaces deprecated @app.on_event) ────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    try:
        model = PlantDiseaseModel(MODEL_PATH)
        if model.is_loaded:
            logger.info("Model loaded successfully from %s", MODEL_PATH)
        else:
            logger.warning("Model not found at %s — running in DEMO mode", MODEL_PATH)
    except Exception as e:
        logger.error("Failed to initialize model: %s", e)
        model = PlantDiseaseModel(None)
    yield
    logger.info("Server shutting down")


app = FastAPI(
    title="PlantDiseases API",
    description="AI-powered plant disease detection for houseplants",
    version="1.0.0",
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
    """Health check endpoint — returns server status and model state."""
    return {
        "status": "ok",
        "model_loaded": model is not None and model.is_loaded,
        "version": "1.0.0",
        "num_classes": len(model.CLASS_NAMES) if model else 0,
    }


@app.post("/api/analyze")
async def analyze_image(image: UploadFile = File(...)):
    """Analyze a plant image for diseases.

    Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
    Returns bilingual (EN/RU) disease diagnosis with treatment advice.
    """
    start_time = time.time()

    # ── Input validation ─────────────────────────────────────────
    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        logger.warning("Rejected file with content_type=%s", image.content_type)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format. Allowed: JPEG, PNG, WebP, BMP",
        )

    # Read and check file size
    contents = await image.read()
    if len(contents) > MAX_FILE_SIZE:
        logger.warning("Rejected oversized file: %d bytes", len(contents))
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)} MB",
        )

    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file received")

    # ── Image processing ─────────────────────────────────────────
    try:
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception:
        logger.error("Failed to decode image from %s", image.filename)
        raise HTTPException(status_code=400, detail="Cannot read image file")

    # ── Prediction ───────────────────────────────────────────────
    prediction = model.predict(img)

    class_name = prediction["class_name"]
    confidence = prediction["confidence"]

    # Look up disease info
    disease_info = DISEASES_DATABASE.get(class_name, DISEASES_DATABASE["unknown"])

    elapsed = time.time() - start_time
    logger.info(
        "Analysis complete: class=%s confidence=%.3f time=%.2fs file=%s",
        class_name, confidence, elapsed, image.filename,
    )

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
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
