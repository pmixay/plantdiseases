"""
Stage 1 — Disease Region Detector.

Uses MobileNetV3-Small as a binary classifier (healthy / diseased)
with Grad-CAM to localise the affected area on the leaf.

When no trained model is found, falls back to colour-based heuristics
that look for brown, yellow, and white patches typical of plant diseases.
"""

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torchvision import models, transforms

logger = logging.getLogger(__name__)

IMG_SIZE = 224


# ── Grad-CAM helper ─────────────────────────────────────────────────

class GradCAM:
    """Generate class-activation heatmaps from a target conv layer."""

    def __init__(self, model: nn.Module, target_layer: nn.Module):
        self.model = model
        self.activations: Optional[torch.Tensor] = None
        self.gradients: Optional[torch.Tensor] = None

        target_layer.register_forward_hook(self._save_activation)
        target_layer.register_full_backward_hook(self._save_gradient)

    def _save_activation(self, _module, _input, output):
        self.activations = output.detach()

    def _save_gradient(self, _module, _grad_in, grad_out):
        self.gradients = grad_out[0].detach()

    @torch.enable_grad()
    def generate(self, input_tensor: torch.Tensor, target_class: int = 1):
        """Return a normalised heatmap (H×W float32 in [0, 1])."""
        self.model.zero_grad()
        output = self.model(input_tensor)
        score = output[0, target_class]
        score.backward()

        weights = self.gradients.mean(dim=[2, 3], keepdim=True)
        cam = (weights * self.activations).sum(dim=1, keepdim=True)
        cam = F.relu(cam)
        cam = cam.squeeze().cpu().numpy()

        if cam.max() > 0:
            cam = cam / cam.max()
        return cam.astype(np.float32)


# ── Bounding-box extraction from heatmap ─────────────────────────────

def heatmap_to_bbox(
    heatmap: np.ndarray,
    original_w: int,
    original_h: int,
    threshold: float = 0.4,
    padding_ratio: float = 0.15,
) -> Optional[dict]:
    """Convert a Grad-CAM heatmap into a bounding box in original-image coords.

    Returns ``None`` when the heatmap has no strong activation.
    """
    resized = cv2.resize(heatmap, (original_w, original_h))
    binary = (resized > threshold).astype(np.uint8)

    coords = np.where(binary > 0)
    if len(coords[0]) == 0:
        return None

    y_min, y_max = int(coords[0].min()), int(coords[0].max())
    x_min, x_max = int(coords[1].min()), int(coords[1].max())

    # Add padding
    pad_x = int((x_max - x_min) * padding_ratio)
    pad_y = int((y_max - y_min) * padding_ratio)
    x_min = max(0, x_min - pad_x)
    y_min = max(0, y_min - pad_y)
    x_max = min(original_w, x_max + pad_x)
    y_max = min(original_h, y_max + pad_y)

    return {
        "x": x_min,
        "y": y_min,
        "width": x_max - x_min,
        "height": y_max - y_min,
    }


# ── Main detector class ─────────────────────────────────────────────

class DiseaseDetector:
    """Binary healthy/diseased detector with region localisation."""

    LABELS = ["healthy", "diseased"]

    def __init__(self, model_path: Path | None = None):
        self.model: Optional[nn.Module] = None
        self.grad_cam: Optional[GradCAM] = None
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
                logger.error("Failed to load detector model: %s", e)

        if not self.is_loaded:
            logger.warning("Detector: no model — running in DEMO mode")

    def _load_model(self, path: Path):
        net = models.mobilenet_v3_small(weights=None)
        net.classifier[-1] = nn.Linear(net.classifier[-1].in_features, 2)

        state = torch.load(str(path), map_location=self.device, weights_only=True)
        net.load_state_dict(state)
        net.to(self.device).eval()

        self.model = net
        self.grad_cam = GradCAM(net, net.features[-1])
        self.is_loaded = True
        logger.info("Detector model loaded from %s", path)

    # ── public API ───────────────────────────────────────────────────

    def detect(self, img: Image.Image) -> dict:
        """Analyse the full image and return detection results.

        Returns
        -------
        dict with keys:
            is_diseased : bool
            confidence  : float
            region      : dict | None   (bounding box in original coords)
        """
        if self.is_loaded:
            return self._detect_real(img)
        return self._detect_demo(img)

    # ── real inference ───────────────────────────────────────────────

    def _detect_real(self, img: Image.Image) -> dict:
        tensor = self.transform(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits = self.model(tensor)
        probs = F.softmax(logits, dim=1)[0]
        is_diseased = bool(probs[1] > probs[0])
        confidence = float(probs[1] if is_diseased else probs[0])

        region = None
        if is_diseased:
            heatmap = self.grad_cam.generate(tensor, target_class=1)
            region = heatmap_to_bbox(heatmap, img.width, img.height)

        return {
            "is_diseased": is_diseased,
            "confidence": round(confidence, 4),
            "region": region,
        }

    # ── demo mode (colour heuristics) ───────────────────────────────

    def _detect_demo(self, img: Image.Image) -> dict:
        arr = np.array(img.resize((256, 256)))
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

        # Masks for suspicious colours
        # Brown spots (low saturation, medium value)
        brown = cv2.inRange(hsv, (8, 40, 40), (25, 200, 180))
        # Yellow patches
        yellow = cv2.inRange(hsv, (20, 80, 100), (35, 255, 255))
        # White/grey patches (powdery mildew)
        white = cv2.inRange(hsv, (0, 0, 180), (180, 50, 255))
        # Dark necrotic spots
        dark = cv2.inRange(hsv, (0, 0, 0), (180, 255, 50))

        combined = cv2.bitwise_or(brown, yellow)
        combined = cv2.bitwise_or(combined, white)
        combined = cv2.bitwise_or(combined, dark)

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)

        disease_ratio = combined.sum() / 255.0 / (256 * 256)

        if disease_ratio < 0.05:
            return {
                "is_diseased": False,
                "confidence": round(0.70 + np.random.uniform(0, 0.20), 4),
                "region": None,
            }

        # Extract bounding box from the largest connected component
        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"is_diseased": True, "confidence": 0.60, "region": None}

        largest = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(largest)

        # Scale back to original image size
        sx, sy = img.width / 256, img.height / 256
        pad = 0.15
        x1 = max(0, int((x - w * pad) * sx))
        y1 = max(0, int((y - h * pad) * sy))
        x2 = min(img.width, int((x + w * (1 + pad)) * sx))
        y2 = min(img.height, int((y + h * (1 + pad)) * sy))

        return {
            "is_diseased": True,
            "confidence": round(0.60 + np.random.uniform(0, 0.25), 4),
            "region": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
        }
