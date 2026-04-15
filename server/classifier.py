"""
Stage 2 — Disease Classifier.

Uses EfficientNet-B0 to classify a cropped leaf region into one of 15
disease classes.  Receives the ROI extracted by Stage 1 (detector) so
the classifier works on the most informative part of the image.

When no trained model is found, falls back to colour-based heuristics.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torchvision import models, transforms

logger = logging.getLogger(__name__)

IMG_SIZE = 224

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

NUM_CLASSES = len(CLASS_NAMES)


class DiseaseClassifier:
    """Fine-grained 15-class plant disease classifier."""

    CLASS_NAMES = CLASS_NAMES

    def __init__(self, model_path: Path | None = None):
        self.model: Optional[nn.Module] = None
        self.is_loaded = False
        self.device = torch.device("cpu")

        self.transform = transforms.Compose([
            transforms.Resize((IMG_SIZE, IMG_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

        if model_path and model_path.exists():
            try:
                self._load_model(model_path)
            except Exception as e:
                logger.error("Failed to load classifier model: %s", e)

        if not self.is_loaded:
            logger.warning("Classifier: no model — running in DEMO mode")

    def _load_model(self, path: Path):
        net = models.efficientnet_b0(weights=None)
        net.classifier[-1] = nn.Linear(net.classifier[-1].in_features, NUM_CLASSES)

        state = torch.load(str(path), map_location=self.device, weights_only=True)
        net.load_state_dict(state)
        net.to(self.device).eval()

        self.model = net
        self.is_loaded = True
        logger.info("Classifier model loaded from %s", path)

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        """Classify a (preferably cropped) leaf image.

        Returns
        -------
        dict with keys:
            class_name : str
            confidence : float
            all_probs  : dict[str, float]  (every class → probability)
        """
        if self.is_loaded:
            return self._classify_real(img)
        return self._classify_demo(img)

    # ── real inference ───────────────────────────────────────────────

    @torch.no_grad()
    def _classify_real(self, img: Image.Image) -> dict:
        tensor = self.transform(img).unsqueeze(0).to(self.device)
        logits = self.model(tensor)
        probs = F.softmax(logits, dim=1)[0].cpu().numpy()

        top_idx = int(np.argmax(probs))
        return {
            "class_name": CLASS_NAMES[top_idx],
            "confidence": round(float(probs[top_idx]), 4),
            "all_probs": {
                CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(NUM_CLASSES)
            },
        }

    # ── demo mode (colour heuristics) ───────────────────────────────

    def _classify_demo(self, img: Image.Image) -> dict:
        arr = np.array(img.resize((64, 64)), dtype=np.float32)
        r_mean = arr[:, :, 0].mean()
        g_mean = arr[:, :, 1].mean()
        b_mean = arr[:, :, 2].mean()
        total = r_mean + g_mean + b_mean + 1e-6

        green_ratio = g_mean / total
        brown_ratio = r_mean / total
        brightness = total / 3.0

        # Very green → healthy
        if green_ratio > 0.38:
            cls = "healthy"
            conf = 0.82 + np.random.uniform(0, 0.15)
        # Bright white-ish → powdery mildew
        elif brightness > 190 and g_mean < 170:
            cls = np.random.choice(["powdery_mildew", "botrytis"])
            conf = 0.60 + np.random.uniform(0, 0.25)
        # Brown tones → fungal
        elif brown_ratio > 0.42:
            cls = np.random.choice([
                "early_blight", "late_blight", "leaf_mold",
                "septoria_leaf_spot", "root_rot",
            ])
            conf = 0.65 + np.random.uniform(0, 0.25)
        # Reddish → rust / spots
        elif r_mean > g_mean and r_mean > 140:
            cls = np.random.choice(["rust", "bacterial_spot", "anthracnose"])
            conf = 0.60 + np.random.uniform(0, 0.25)
        # Yellowish → viral
        else:
            cls = np.random.choice([
                "yellow_leaf_curl", "mosaic_virus",
                "spider_mites", "target_spot",
            ])
            conf = 0.55 + np.random.uniform(0, 0.25)

        # Build pseudo-probabilities
        probs = {name: 0.0 for name in CLASS_NAMES}
        probs[cls] = round(conf, 4)
        leftover = 1.0 - conf
        others = [n for n in CLASS_NAMES if n != cls]
        rnd = np.random.dirichlet(np.ones(len(others))) * leftover
        for name, p in zip(others, rnd):
            probs[name] = round(float(p), 4)

        return {
            "class_name": cls,
            "confidence": round(float(conf), 4),
            "all_probs": probs,
        }
