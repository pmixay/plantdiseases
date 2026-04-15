"""
PlantDiseaseModel — wraps a TensorFlow/Keras CNN for plant disease classification.

The model is trained on PlantVillage dataset, fine-tuned for houseplant diseases.
If no trained model is found, runs in demo mode with random predictions.
"""

import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Class labels matching the PlantVillage subset used for training
# Focused on common houseplant-relevant diseases
CLASS_NAMES = [
    "healthy",
    "bacterial_spot",
    "early_blight",
    "late_blight",
    "leaf_mold",
    "septoria_leaf_spot",
    "spider_mites",
    "target_spot",
    "mosaic_virus",
    "yellow_leaf_curl",
    "powdery_mildew",
    "rust",
    "root_rot",
    "anthracnose",
    "botrytis",
]

IMG_SIZE = 224  # Input size for the CNN


class PlantDiseaseModel:
    """Handles model loading and image prediction."""

    CLASS_NAMES = CLASS_NAMES

    def __init__(self, model_path: Path | None):
        self.model = None
        self.is_loaded = False

        if model_path and model_path.exists():
            try:
                import tensorflow as tf
                self.model = tf.keras.models.load_model(str(model_path))
                self.is_loaded = True
                logger.info(f"Model loaded from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
        else:
            logger.warning("No model file found — running in DEMO mode")

    def preprocess(self, img: Image.Image) -> np.ndarray:
        """Resize and normalize image for the CNN."""
        img = img.resize((IMG_SIZE, IMG_SIZE))
        arr = np.array(img, dtype=np.float32) / 255.0
        return np.expand_dims(arr, axis=0)  # batch dim

    def predict(self, img: Image.Image) -> dict:
        """
        Predict disease from a PIL Image.
        Returns dict with 'class_name' and 'confidence'.
        """
        if self.is_loaded and self.model is not None:
            return self._predict_real(img)
        else:
            return self._predict_demo(img)

    def _predict_real(self, img: Image.Image) -> dict:
        """Run actual model inference."""
        batch = self.preprocess(img)
        predictions = self.model.predict(batch, verbose=0)
        probs = predictions[0]

        top_idx = int(np.argmax(probs))
        confidence = float(probs[top_idx])

        return {
            "class_name": CLASS_NAMES[top_idx],
            "confidence": confidence,
            "all_probs": {CLASS_NAMES[i]: float(probs[i]) for i in range(len(CLASS_NAMES))}
        }

    def _predict_demo(self, img: Image.Image) -> dict:
        """
        Demo mode: analyze image color distribution to give plausible results.
        Uses simple heuristics based on green/brown/yellow ratios.
        """
        img_small = img.resize((64, 64))
        arr = np.array(img_small, dtype=np.float32)

        # Calculate average color channels
        r_mean = arr[:, :, 0].mean()
        g_mean = arr[:, :, 1].mean()
        b_mean = arr[:, :, 2].mean()

        # Simple heuristic based on color
        green_ratio = g_mean / (r_mean + g_mean + b_mean + 1e-6)
        brown_ratio = r_mean / (r_mean + g_mean + b_mean + 1e-6)

        if green_ratio > 0.38:
            # Mostly green → likely healthy
            return {"class_name": "healthy", "confidence": 0.82 + np.random.uniform(0, 0.15)}
        elif brown_ratio > 0.42:
            # Brownish tones → possible disease
            diseases = ["early_blight", "leaf_mold", "septoria_leaf_spot", "root_rot"]
            choice = np.random.choice(diseases)
            return {"class_name": choice, "confidence": 0.65 + np.random.uniform(0, 0.25)}
        elif r_mean > g_mean and r_mean > 140:
            # Reddish → rust or spots
            diseases = ["rust", "bacterial_spot", "anthracnose"]
            choice = np.random.choice(diseases)
            return {"class_name": choice, "confidence": 0.6 + np.random.uniform(0, 0.3)}
        else:
            # Yellow tones or unclear
            diseases = ["yellow_leaf_curl", "mosaic_virus", "powdery_mildew", "spider_mites"]
            choice = np.random.choice(diseases)
            return {"class_name": choice, "confidence": 0.55 + np.random.uniform(0, 0.3)}
