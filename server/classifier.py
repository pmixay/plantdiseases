"""
Stage 2 — Houseplant Disease Classifier.

Uses EfficientNetV2-S to classify a cropped leaf region into one of 9
houseplant-focused disease classes, including a rejection class
(``not_a_plant``) that lets the pipeline stay robust to fingers, walls
and random objects in the frame.

Class names are loaded from ``models/classes.json`` (produced by
``train_notebook.ipynb`` in Colab). If that file is missing, a built-in
default list is used. ``DEFAULT_CLASS_NAMES`` mirrors the alphabetical
order that ``ImageFolder`` produces in the notebook, so a freshly
trained checkpoint will line up with the defaults even without
classes.json. When no trained weights are available the classifier
falls back to colour heuristics so the server is usable in demo mode.
"""

from __future__ import annotations

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

IMG_SIZE = 300  # EfficientNetV2-S native inference size

# Alphabetical — must match training-time ``sorted(ImageFolder.classes)``.
# 9 classes total; ``spider_mites`` is the new pest class produced by the
# v3.1 notebook. The 8-class v3.0 checkpoint still loads cleanly because
# ``_load_model`` truncates the list when the head is smaller.
DEFAULT_CLASS_NAMES = [
    "blight",
    "healthy",
    "leaf_mold",
    "leaf_spot",
    "mosaic_virus",
    "not_a_plant",
    "powdery_mildew",
    "rust",
    "spider_mites",
]

# Minimum confidence below which we surface the built-in fallback name.
LOW_CONFIDENCE_THRESHOLD = 0.35


def _load_class_names(model_path: Path | None) -> list[str]:
    """Load class names from classes.json next to the checkpoint."""
    if model_path is not None:
        meta = model_path.parent / "classes.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                names = data.get("class_names") or DEFAULT_CLASS_NAMES
                version = data.get("model_version", "unknown")
                logger.info(
                    "Loaded %d class names from %s (model_version=%s)",
                    len(names), meta, version,
                )
                return list(names)
            except Exception as e:
                logger.warning("Failed to read %s: %s — using defaults", meta, e)
    return list(DEFAULT_CLASS_NAMES)


def _build_efficientnet_v2_s(num_classes: int) -> nn.Module:
    """Create an untrained EfficientNetV2-S with a classifier head sized
    to ``num_classes``."""
    net = models.efficientnet_v2_s(weights=None)
    in_features = net.classifier[-1].in_features
    net.classifier[-1] = nn.Linear(in_features, num_classes)
    return net


class DiseaseClassifier:
    """EfficientNetV2-S houseplant disease classifier."""

    def __init__(self, model_path: Path | None = None):
        self.model: Optional[nn.Module] = None
        self.is_loaded = False
        self.device = torch.device("cpu")

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

    def _load_model(self, path: Path) -> None:
        state = torch.load(str(path), map_location=self.device, weights_only=True)

        # Sanity-check the head width against our class list.
        head_weight = None
        for key, tensor in state.items():
            if key.startswith("classifier.") and key.endswith(".weight") and tensor.ndim == 2:
                head_weight = tensor
        if head_weight is not None:
            state_num_classes = int(head_weight.shape[0])
            if state_num_classes != self.NUM_CLASSES:
                logger.error(
                    "Classifier mismatch: checkpoint has %d outputs but classes.json "
                    "declares %d. Trusting the checkpoint and padding class list — "
                    "regenerate classes.json for production deployments.",
                    state_num_classes, self.NUM_CLASSES,
                )
                if state_num_classes <= len(self.CLASS_NAMES):
                    self.CLASS_NAMES = self.CLASS_NAMES[:state_num_classes]
                else:
                    self.CLASS_NAMES = self.CLASS_NAMES + [
                        f"class_{i}" for i in range(len(self.CLASS_NAMES), state_num_classes)
                    ]
                self.NUM_CLASSES = state_num_classes

        net = _build_efficientnet_v2_s(self.NUM_CLASSES)
        net.load_state_dict(state)
        net.to(self.device).eval()

        self.model = net
        self.is_loaded = True
        logger.info(
            "Classifier (EfficientNetV2-S) loaded from %s (%d classes)",
            path, self.NUM_CLASSES,
        )

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        """Classify a (preferably cropped) leaf image.

        Returns
        -------
        dict with:
            class_name : str                  — top-1 class
            confidence : float                — top-1 probability
            all_probs  : dict[str, float]     — probability per class
            low_confidence : bool             — True when top-1 < threshold
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
        top_conf = float(probs[top_idx])
        return {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
            "low_confidence": top_conf < LOW_CONFIDENCE_THRESHOLD,
        }

    # ── demo mode (colour heuristics, deterministic per image) ───────

    @staticmethod
    def _image_seed(img: Image.Image) -> int:
        small = np.array(img.resize((16, 16)), dtype=np.uint8)
        return int(small.sum()) % (2**31)

    def _classify_demo(self, img: Image.Image) -> dict:
        rng = np.random.RandomState(self._image_seed(img))
        arr = np.array(img.resize((64, 64)), dtype=np.float32)
        r_mean = float(arr[:, :, 0].mean())
        g_mean = float(arr[:, :, 1].mean())
        b_mean = float(arr[:, :, 2].mean())
        total = r_mean + g_mean + b_mean + 1e-6

        green_ratio = g_mean / total
        brown_ratio = r_mean / total
        brightness = total / 3.0

        names = self.CLASS_NAMES
        if not names:
            return {
                "class_name": "unknown",
                "confidence": 0.0,
                "all_probs": {},
                "low_confidence": True,
            }

        # Almost no green → probably not a plant.
        if green_ratio < 0.32 and "not_a_plant" in names:
            cls = "not_a_plant"
            conf = 0.60 + rng.uniform(0, 0.20)
        elif green_ratio > 0.42 and "healthy" in names:
            cls = "healthy"
            conf = 0.78 + rng.uniform(0, 0.15)
        elif brightness > 190 and g_mean < 170:
            pool = [n for n in ("powdery_mildew", "leaf_mold") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif brown_ratio > 0.42:
            pool = [n for n in ("blight", "leaf_spot", "leaf_mold") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif r_mean > g_mean and r_mean > 140:
            pool = [n for n in ("rust", "leaf_spot") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        else:
            pool = [n for n in ("mosaic_virus", "spider_mites") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.50 + rng.uniform(0, 0.25)

        probs = {name: 0.0 for name in names}
        probs[cls] = round(conf, 4)
        leftover = 1.0 - conf
        others = [n for n in names if n != cls]
        if others:
            rnd = rng.dirichlet(np.ones(len(others))) * leftover
            for name, p in zip(others, rnd, strict=False):
                probs[name] = round(float(p), 4)

        return {
            "class_name": cls,
            "confidence": round(float(conf), 4),
            "all_probs": probs,
            "low_confidence": conf < LOW_CONFIDENCE_THRESHOLD,
        }
