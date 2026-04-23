"""
Stage 1 — Disease Region Detector.

Binary classifier (healthy vs. diseased) on MobileNetV3-Small with
Grad-CAM localisation. When no trained weights are present, falls back
to an HSV-based colour heuristic that flags brown/yellow/white/dark
patches typical of leaf disease.
"""

import logging
import threading
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


class GradCAM:
    """Thread-safe Grad-CAM using forward/backward hooks on a target layer.

    Hooks capture per-call activations and gradients into thread-local
    storage so concurrent requests cannot overwrite each other's state.
    """

    def __init__(self, model: nn.Module, target_layer: nn.Module):
        self.model = model
        self._local = threading.local()

        target_layer.register_forward_hook(self._save_activation)
        target_layer.register_full_backward_hook(self._save_gradient)

    def _save_activation(self, _module, _input, output):
        self._local.activations = output.detach()

    def _save_gradient(self, _module, _grad_in, grad_out):
        self._local.gradients = grad_out[0].detach()

    @torch.enable_grad()
    def generate(self, input_tensor: torch.Tensor, target_class: int = 1) -> np.ndarray:
        """Return a normalised heatmap (H×W float32 in [0, 1])."""
        self.model.zero_grad(set_to_none=True)
        output = self.model(input_tensor)
        score = output[0, target_class]
        score.backward()

        activations = getattr(self._local, "activations", None)
        gradients = getattr(self._local, "gradients", None)
        if activations is None or gradients is None:
            return np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.float32)

        weights = gradients.mean(dim=[2, 3], keepdim=True)
        cam = (weights * activations).sum(dim=1, keepdim=True)
        cam = F.relu(cam).squeeze().cpu().numpy()
        if cam.max() > 0:
            cam = cam / cam.max()
        return cam.astype(np.float32)


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

    pad_x = int((x_max - x_min) * padding_ratio)
    pad_y = int((y_max - y_min) * padding_ratio)
    x_min = max(0, x_min - pad_x)
    y_min = max(0, y_min - pad_y)
    x_max = min(original_w, x_max + pad_x)
    y_max = min(original_h, y_max + pad_y)

    severity = float((resized > threshold).sum()) / float(resized.size)

    return {
        "x": x_min,
        "y": y_min,
        "width": x_max - x_min,
        "height": y_max - y_min,
        "severity": round(severity, 4),
    }


class DiseaseDetector:
    """Binary healthy/diseased detector with ROI localisation."""

    LABELS = ["healthy", "diseased"]

    def __init__(self, model_path: Path | None = None):
        self.model: Optional[nn.Module] = None
        self.grad_cam: Optional[GradCAM] = None
        self.is_loaded = False
        self.device = torch.device("cpu")
        # Serialise grad-CAM calls — backward pass is not re-entrant.
        self._inference_lock = threading.Lock()

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
            logger.warning("Detector: no model found, running in DEMO mode")

    def _load_model(self, path: Path):
        state = torch.load(str(path), map_location=self.device, weights_only=True)

        # The detector must be binary; refuse to load anything else.
        head_weight = None
        for key, tensor in state.items():
            if key.startswith("classifier.") and key.endswith(".weight") and tensor.ndim == 2:
                head_weight = tensor
        if head_weight is not None and int(head_weight.shape[0]) != 2:
            raise ValueError(
                f"Detector checkpoint has {int(head_weight.shape[0])} outputs, expected 2 "
                f"(healthy/diseased). Retrain with `python train.py --detector`."
            )

        net = models.mobilenet_v3_small(weights=None)
        net.classifier[-1] = nn.Linear(net.classifier[-1].in_features, 2)
        net.load_state_dict(state)
        net.to(self.device).eval()

        self.model = net
        self.grad_cam = GradCAM(net, net.features[-1])
        self.is_loaded = True
        logger.info("Detector model loaded from %s", path)

    def detect(self, img: Image.Image) -> dict:
        """Analyse an image. Returns {is_diseased, confidence, region}."""
        if self.is_loaded:
            return self._detect_real(img)
        return self._detect_demo(img)

    def _detect_real(self, img: Image.Image) -> dict:
        tensor = self.transform(img).unsqueeze(0).to(self.device)

        with self._inference_lock:
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

    @staticmethod
    def _image_seed(img: Image.Image) -> int:
        small = np.array(img.resize((16, 16)), dtype=np.uint8)
        return int(small.sum()) % (2**31)

    def _detect_demo(self, img: Image.Image) -> dict:
        rng = np.random.RandomState(self._image_seed(img))
        arr = np.array(img.resize((256, 256)))
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

        brown = cv2.inRange(hsv, (8, 40, 40), (25, 200, 180))
        yellow = cv2.inRange(hsv, (20, 80, 100), (35, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 180), (180, 50, 255))
        dark = cv2.inRange(hsv, (0, 0, 0), (180, 255, 50))

        combined = cv2.bitwise_or(brown, yellow)
        combined = cv2.bitwise_or(combined, white)
        combined = cv2.bitwise_or(combined, dark)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)

        disease_ratio = combined.sum() / 255.0 / (256 * 256)

        if disease_ratio < 0.05:
            return {
                "is_diseased": False,
                "confidence": round(0.70 + rng.uniform(0, 0.20), 4),
                "region": None,
            }

        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"is_diseased": True, "confidence": 0.60, "region": None}

        largest = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(largest)

        sx, sy = img.width / 256, img.height / 256
        pad = 0.15
        x1 = max(0, int((x - w * pad) * sx))
        y1 = max(0, int((y - h * pad) * sy))
        x2 = min(img.width, int((x + w * (1 + pad)) * sx))
        y2 = min(img.height, int((y + h * (1 + pad)) * sy))

        return {
            "is_diseased": True,
            "confidence": round(0.60 + rng.uniform(0, 0.25), 4),
            "region": {
                "x": x1,
                "y": y1,
                "width": x2 - x1,
                "height": y2 - y1,
                "severity": round(float(disease_ratio), 4),
            },
        }
