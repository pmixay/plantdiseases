"""
Stage 2 — Disease Classifier.

Uses EfficientNet-B0 to classify a cropped leaf region into disease
classes.  Receives the ROI extracted by Stage 1 (detector) so the
classifier works on the most informative part of the image.

Class names are loaded from ``models/classes.json`` (produced by
the training notebook).  If that file is missing, a built-in default
list of 15 classes is used.

When no trained model is found, falls back to colour-based heuristics.
"""

import json
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

DEFAULT_CLASS_NAMES = [
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


def _load_class_names(model_path: Path | None) -> list[str]:
    """Try to load class names from classes.json next to the model file."""
    if model_path is not None:
        meta = model_path.parent / "classes.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text())
                names = data.get("class_names", DEFAULT_CLASS_NAMES)
                logger.info("Loaded %d class names from %s", len(names), meta)
                return names
            except Exception as e:
                logger.warning("Failed to read %s: %s — using defaults", meta, e)
    return list(DEFAULT_CLASS_NAMES)


class DiseaseClassifier:
    """Fine-grained plant disease classifier (dynamic number of classes)."""

    def __init__(self, model_path: Path | None = None):
        self.model: Optional[nn.Module] = None
        self.is_loaded = False
        self.device = torch.device("cpu")

        # Resolve class names first — needed to build the correct head
        self.CLASS_NAMES = _load_class_names(model_path)
        self.NUM_CLASSES = len(self.CLASS_NAMES)

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
        net.classifier[-1] = nn.Linear(net.classifier[-1].in_features, self.NUM_CLASSES)

        state = torch.load(str(path), map_location=self.device, weights_only=True)
        net.load_state_dict(state)
        net.to(self.device).eval()

        self.model = net
        self.is_loaded = True
        logger.info("Classifier model loaded from %s (%d classes)", path, self.NUM_CLASSES)

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        """Classify a (preferably cropped) leaf image.

        Returns
        -------
        dict with keys:
            class_name : str
            confidence : float
            all_probs  : dict[str, float]  (every class -> probability)
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
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(float(probs[top_idx]), 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
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

        # Pick a plausible class from whatever names we have
        names = self.CLASS_NAMES
        if not names:
            return {
                "class_name": "unknown",
                "confidence": 0.0,
                "all_probs": {},
            }

        if green_ratio > 0.38 and "healthy" in names:
            cls = "healthy"
            conf = 0.82 + np.random.uniform(0, 0.15)
        elif brightness > 190 and g_mean < 170:
            pool = [n for n in ("powdery_mildew", "botrytis", "leaf_mold") if n in names]
            cls = np.random.choice(pool) if pool else names[0]
            conf = 0.60 + np.random.uniform(0, 0.25)
        elif brown_ratio > 0.42:
            pool = [n for n in ("early_blight", "late_blight", "leaf_mold",
                                "septoria_leaf_spot", "root_rot") if n in names]
            cls = np.random.choice(pool) if pool else names[0]
            conf = 0.65 + np.random.uniform(0, 0.25)
        elif r_mean > g_mean and r_mean > 140:
            pool = [n for n in ("rust", "bacterial_spot", "anthracnose") if n in names]
            cls = np.random.choice(pool) if pool else names[0]
            conf = 0.60 + np.random.uniform(0, 0.25)
        else:
            pool = [n for n in ("yellow_leaf_curl", "mosaic_virus",
                                "spider_mites", "target_spot") if n in names]
            cls = np.random.choice(pool) if pool else names[0]
            conf = 0.55 + np.random.uniform(0, 0.25)

        # Build pseudo-probabilities
        probs = {name: 0.0 for name in names}
        probs[cls] = round(conf, 4)
        leftover = 1.0 - conf
        others = [n for n in names if n != cls]
        if others:
            rnd = np.random.dirichlet(np.ones(len(others))) * leftover
            for name, p in zip(others, rnd):
                probs[name] = round(float(p), 4)

        return {
            "class_name": cls,
            "confidence": round(float(conf), 4),
            "all_probs": probs,
        }
