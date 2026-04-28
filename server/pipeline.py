"""
Two-stage houseplant disease detection pipeline (v4.1).

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``leaf`` bboxes.
Stage 2 (Classifier) — EfficientNet-B0 (ONNX). Classifies a cropped
                       leaf region into one of up to 11 houseplant
                       condition classes including ``not_a_plant``.

v4.1 design changes vs v4:

* **Mean voting** instead of max-disease primary-box selection. v4
  picked the bbox with the highest non-healthy softmax mass — which
  is, by construction, biased toward "found a disease". A single
  noisy edge crop with ``leaf_spot=0.8`` would outvote nine clean
  ``healthy=0.7`` predictions. v4.1 averages softmax distributions
  across valid bboxes; one outlier no longer wins the room.
* **Size filter** on bboxes. Tiny detections (<1% of image area or
  <32px) are dropped before classification — they were the dominant
  source of v4 false-positives on real indoor photos.
* **Healthy fallback margin**. When the averaged top-1 is a disease
  but its margin over ``healthy`` is below ``HEALTHY_FALLBACK_MARGIN``
  the prediction is downgraded to ``healthy``. This compensates for
  the residual overconfidence of EfficientNet on out-of-distribution
  inputs without hard-rerouting (which v3 did and v4 explicitly
  removed).
* **Disease confidence threshold**. If top-1 is a disease but the
  averaged confidence is below ``DISEASE_MIN_CONFIDENCE`` we surface
  ``healthy`` and attach a soft warning. The model already gates
  ``low_confidence`` on per-crop level; this is the per-image gate.
* **Temperature scaling** (optional). If ``temperature.json`` is
  present next to the classifier weights, probabilities are recalibrated
  before voting. Models trained with v4.1 notebook produce this file
  automatically; older v4 models default to ``T=1.0`` (no-op).

Stage 1 detector still owns localisation only — the disease decision
remains 100% on the classifier side, as in v4.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from classifier import DiseaseClassifier
from detector import LeafDetector

logger = logging.getLogger(__name__)

MIN_CROP_SIZE = 32
CROP_PADDING_RATIO = 0.10

# v4.6: bboxes smaller than this fraction of the full image area
# get dropped before classification. v4.1 used 0.01 which was too
# aggressive on wide shots — a 4032×3024 phone photo at 1% = 350×350,
# i.e. nearly the entire visible leaf at typical shooting distance.
# Real noise bboxes (corner FPs, lighting artefacts) are almost
# always <0.5% of the image; 0.005 is the natural break point.
MIN_BBOX_AREA_FRAC = 0.005

# v4 healthy gate (kept). These trigger the soft ``uncertain_healthy``
# warning when top-1 is healthy but weak.
HEALTHY_MIN_CONFIDENCE = 0.55
HEALTHY_MARGIN = 0.10

# v4.1 new: disease-side gate. If averaged top-1 is a disease but its
# softmax mass is below this threshold, we downgrade to healthy.
# Calibrated against v4 misfires on backlit / indoor / blurry shots
# where the model gives ~50-60% to a disease but ~40% to healthy —
# the right call there is "not confident enough to claim disease".
DISEASE_MIN_CONFIDENCE = 0.65

# v4.1 new: if averaged top-1 is a disease but its margin over
# ``healthy`` is below this, downgrade. Catches the case where the
# model is split close to 50/50 between a disease and healthy across
# the image — there's no good reason to commit to disease then.
HEALTHY_FALLBACK_MARGIN = 0.15

# Classes that are not "diseases" — used to compute disease scores
# and to know when to apply the healthy fallback.
_NON_DISEASE = {"healthy", "not_a_plant"}

TOP_K_ALTERNATIVES = 3


def _load_temperature(classifier_path: Path | None) -> float:
    """Look for ``temperature.json`` next to the classifier weights.

    v4.1 training notebook writes it automatically. Returns 1.0 (no-op)
    for v4 models or when the file is missing/malformed.
    """
    candidates: list[Path] = []
    if classifier_path is not None:
        candidates.append(Path(classifier_path).parent / "temperature.json")
    candidates.extend([
        Path("server/models/temperature.json"),
        Path("models/temperature.json"),
    ])
    for p in candidates:
        try:
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                T = float(data.get("temperature", 1.0))
                if T > 0:
                    logger.info("Loaded temperature T=%.3f from %s", T, p)
                    return T
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not load temperature from %s: %s", p, e)
    return 1.0


def _apply_temperature(probs: dict[str, float], T: float) -> dict[str, float]:
    """Apply temperature scaling on already-softmaxed probabilities.

    Mathematically equivalent to ``softmax(logits / T)``: since
    ``softmax(z/T)`` is proportional to ``exp(z/T) = (exp z) ** (1/T) =
    p ** (1/T)`` (up to a per-batch constant that disappears under
    re-normalisation), we can recover calibrated probs from probs alone.
    """
    if abs(T - 1.0) < 1e-6:
        return probs
    powered = {n: max(float(p), 1e-12) ** (1.0 / T) for n, p in probs.items()}
    z = sum(powered.values())
    if z <= 0:
        return probs
    return {n: v / z for n, v in powered.items()}


class PlantDiseasePipeline:
    """Orchestrates the two-stage detection → classification pipeline."""

    def __init__(
        self,
        detector_path: Path | None = None,
        classifier_path: Path | None = None,
    ):
        self.detector = LeafDetector(detector_path)
        self.classifier = DiseaseClassifier(classifier_path)
        # v4.1: opt-in temperature scaling. Defaults to 1.0 (no-op) if
        # temperature.json is absent — keeps backward-compat with v4.
        self.temperature = _load_temperature(classifier_path)
        logger.info(
            "Pipeline ready  detector=%s  classifier=%s (%s backend)  T=%.3f",
            "MODEL" if self.detector.is_loaded else "DEMO",
            "MODEL" if self.classifier.is_loaded else "DEMO",
            getattr(self.classifier, "backend", "demo"),
            self.temperature,
        )

    @property
    def is_loaded(self) -> bool:
        return self.detector.is_loaded or self.classifier.is_loaded

    @property
    def mode(self) -> str:
        if self.detector.is_loaded and self.classifier.is_loaded:
            return "full"
        if self.detector.is_loaded or self.classifier.is_loaded:
            return "partial"
        return "demo"

    # ── main entry point ─────────────────────────────────────────────

    def analyze(self, img: Image.Image) -> dict:
        t0 = time.perf_counter()

        stage1 = self.detector.detect(img)
        detections = stage1["detections"]

        logger.info(
            "Stage 1 → detections=%d  best_conf=%.3f",
            len(detections), stage1["confidence"],
        )

        # No leaf in frame → short-circuit to ``not_a_plant``.
        if not detections:
            elapsed = (time.perf_counter() - t0) * 1000
            probs = {n: 0.0 for n in self.classifier.CLASS_NAMES}
            if "not_a_plant" in probs:
                probs["not_a_plant"] = 1.0
            top_k = _build_top_k(probs, TOP_K_ALTERNATIVES)
            return {
                "class_name": "not_a_plant",
                "confidence": 1.0,
                "all_probs": probs,
                "top_k": top_k,
                "warnings": [],
                "detection": {
                    "is_diseased": False,
                    "detector_confidence": 0.0,
                    "regions": [],
                    "primary_region": None,
                },
                "pipeline_mode": self.mode,
                "elapsed_ms": round(elapsed, 1),
            }

        # v4.1: mean voting across valid bboxes (size-filtered).
        primary, classification = self._pick_primary(img, detections)

        is_diseased = (
            classification["class_name"] not in _NON_DISEASE
            and classification["confidence"] >= HEALTHY_MIN_CONFIDENCE * 0.5
        )

        logger.info(
            "Stage 2 → class=%s  confidence=%.3f  primary_box=%s  voted_over=%d",
            classification["class_name"],
            classification["confidence"],
            primary,
            classification.get("_voted_over", 1),
        )

        final = _apply_postprocessing_rules(
            classification, self.classifier.CLASS_NAMES,
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "detection": {
                "is_diseased": is_diseased,
                "detector_confidence": stage1["confidence"],
                "regions": detections,
                "primary_region": primary,
            },
            "pipeline_mode": self.mode,
            "elapsed_ms": round(elapsed, 1),
        }

    # ── helpers ──────────────────────────────────────────────────────

    # Cap the number of bboxes we re-classify per request. With ONNX
    # INT8 EfficientNet-B0 each call costs ~20-30ms on CPU, so 4 boxes
    # adds ~100ms worst case which is acceptable for our 150ms budget.
    _MAX_RECLASSIFY = 4

    def _pick_primary(self, img: Image.Image, detections: list[dict]):
        """Mean-voting primary classification over valid bboxes (v4.1).

        Pipeline:
          1. Drop bboxes smaller than ``MIN_CROP_SIZE`` or
             ``MIN_BBOX_AREA_FRAC`` of image area. These are noise.
          2. Sort by detector confidence, cap to ``_MAX_RECLASSIFY``.
          3. Classify each, average softmax distributions.
          4. Apply temperature scaling (no-op when T=1.0).
          5. Apply healthy fallback if top-1 is a disease with weak
             margin over ``healthy`` or below ``DISEASE_MIN_CONFIDENCE``.
          6. Pick the primary bbox: the one whose own top-1 matches the
             aggregated top-1 (most "representative"). Falls back to
             the largest bbox if no per-box prediction matches.

        Returns ``(primary_box, classification_dict)``.
        """
        W, H = img.size
        img_area = max(W * H, 1)

        # 1. Size filter — drop noise bboxes.
        sized: list[dict] = []
        for d in detections:
            w = int(d.get("width", 0))
            h = int(d.get("height", 0))
            if w < MIN_CROP_SIZE or h < MIN_CROP_SIZE:
                continue
            if (w * h) / img_area < MIN_BBOX_AREA_FRAC:
                continue
            sized.append(d)

        # If everything got filtered, take the single largest bbox.
        # We never want to skip classification entirely when the detector
        # found leaves — otherwise we'd misroute to ``not_a_plant``.
        if not sized:
            sized = [
                max(
                    detections,
                    key=lambda d: int(d.get("width", 0)) * int(d.get("height", 0)),
                )
            ]

        # 2. Rank by detector confidence and cap.
        ranked = sorted(sized, key=lambda d: -d["confidence"])[: self._MAX_RECLASSIFY]

        # 3. Classify all bboxes in a single batched forward pass when
        # the backend supports dynamic batch (ONNX/PyTorch). Cuts the
        # per-call setup cost on CPU — the dominant overhead for ≤4
        # boxes — without changing numerics.
        crops = [self._extract_crop(img, box) for box in ranked]
        cls_results = self.classifier.classify_batch(crops)

        results: list[tuple[dict, dict]] = []
        sum_probs: dict[str, float] = {n: 0.0 for n in self.classifier.CLASS_NAMES}

        for box, cls_res in zip(ranked, cls_results, strict=False):
            # 4. Per-crop temperature scaling. Doing it before averaging
            # is correct: averaging T-scaled distributions ≠ T-scaling
            # the average.
            calibrated = _apply_temperature(cls_res["all_probs"], self.temperature)
            results.append((box, {**cls_res, "all_probs": calibrated}))
            for n, p in calibrated.items():
                sum_probs[n] = sum_probs.get(n, 0.0) + float(p)

        n_boxes = max(len(results), 1)
        mean_probs = {name: total / n_boxes for name, total in sum_probs.items()}

        # 5. Determine top-1 from the averaged distribution.
        top1_class = max(mean_probs, key=mean_probs.get)
        top1_conf = float(mean_probs[top1_class])
        healthy_p = float(mean_probs.get("healthy", 0.0))

        # 5a. Healthy fallback: if averaged top-1 is a disease but its
        # margin over healthy is small, downgrade. This is the main
        # patch against the v4 false-positive disease pattern.
        #
        # Confidence semantics on fallback: we don't surface the raw
        # ``healthy`` probability — for an overconfident model that's
        # often 3-5%, which reads to the user as "97% sick" even though
        # the actual signal is "we're not confident enough to claim a
        # disease". Instead we report ``1 - top1_disease_conf`` — i.e.
        # how confident we are it is NOT this disease — clamped to be
        # at least the raw healthy prob (we never lie downward) and at
        # most 0.95 (we don't claim certainty we never had).
        downgraded = False
        suspected_disease: Optional[str] = None
        if top1_class not in _NON_DISEASE and "healthy" in mean_probs:
            margin = top1_conf - healthy_p
            disease_top_conf = top1_conf  # remember before we overwrite
            fallback_reason: Optional[str] = None
            if margin < HEALTHY_FALLBACK_MARGIN:
                fallback_reason = f"margin={margin:.3f}<{HEALTHY_FALLBACK_MARGIN}"
            elif top1_conf < DISEASE_MIN_CONFIDENCE:
                fallback_reason = (
                    f"threshold={top1_conf:.3f}<{DISEASE_MIN_CONFIDENCE}"
                )
            if fallback_reason is not None:
                # Confidence = "we are 1-p_disease confident this is NOT
                # the suspected disease", bounded [healthy_p, 0.95].
                inverted_conf = max(healthy_p, 1.0 - disease_top_conf)
                inverted_conf = min(inverted_conf, 0.95)
                logger.info(
                    "Healthy fallback (%s)  was=%s/%.3f  → healthy/%.3f",
                    fallback_reason, top1_class, disease_top_conf, inverted_conf,
                )
                suspected_disease = top1_class
                # Redistribute probability mass so all_probs/top_k stay
                # consistent with the chosen class_name. Without this
                # the UI saw healthy as class_name but the suspected
                # disease still ranked #1 in top_k — a confusing
                # contradiction. We move (disease_p - inverted_conf)
                # from the suspected disease into healthy and
                # renormalize so the distribution still sums to 1.
                target_healthy = max(inverted_conf, healthy_p)
                delta = min(
                    disease_top_conf - inverted_conf,
                    mean_probs[suspected_disease],
                )
                if delta > 0:
                    mean_probs[suspected_disease] -= delta
                    mean_probs["healthy"] = mean_probs.get("healthy", 0.0) + delta
                # Renormalize defensively in case temperature scaling
                # left a sub-1 sum or floating-point drift accumulated.
                total = sum(mean_probs.values())
                if total > 0:
                    mean_probs = {n: p / total for n, p in mean_probs.items()}
                top1_class = "healthy"
                top1_conf = float(mean_probs["healthy"])
                downgraded = True
                # Sanity: confidence we report should never be below
                # the original inverted bound after renormalisation.
                if top1_conf < target_healthy:
                    top1_conf = target_healthy

        # 6. Pick primary bbox: prefer one whose own top-1 matches the
        # aggregated decision (most "representative" of the call).
        # Fall back to the largest bbox if nothing matches.
        matching = [
            (box, cls) for box, cls in results
            if (cls.get("class_name") == top1_class) or (
                # If we downgraded to healthy in aggregation but no
                # per-crop is healthy by argmax (rare), still allow the
                # match by checking whose healthy prob is highest.
                downgraded and top1_class == "healthy"
                and max(cls["all_probs"], key=cls["all_probs"].get) == "healthy"
            )
        ]
        if matching:
            primary_box = max(
                matching,
                key=lambda r: float(r[1]["all_probs"].get(top1_class, 0.0)),
            )[0]
        else:
            primary_box = max(
                results,
                key=lambda r: int(r[0].get("width", 0)) * int(r[0].get("height", 0)),
            )[0]

        # 7. Build aggregated classification dict in the same shape
        # the rest of the pipeline already expects.
        aggregated_cls = {
            "class_name": top1_class,
            "confidence": top1_conf,
            "all_probs": mean_probs,
            "low_confidence": top1_conf < HEALTHY_MIN_CONFIDENCE,
            "_voted_over": n_boxes,  # for logging/telemetry
            "_suspected_disease": suspected_disease,  # set only on healthy fallback
        }
        return primary_box, aggregated_cls

    @staticmethod
    def _extract_crop(img: Image.Image, region: Optional[dict]) -> Image.Image:
        if region is None:
            return img
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w < MIN_CROP_SIZE or h < MIN_CROP_SIZE:
            return img
        pad_x = int(w * CROP_PADDING_RATIO)
        pad_y = int(h * CROP_PADDING_RATIO)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(img.width, x + w + pad_x)
        y2 = min(img.height, y + h + pad_y)
        if x2 <= x1 or y2 <= y1:
            return img
        return img.crop((x1, y1, x2, y2))


# ── pure helpers (exposed for tests) ─────────────────────────────────

def _build_top_k(probs: dict[str, float], k: int) -> list[dict]:
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)[:k]
    return [
        {"class": name, "confidence": round(float(conf), 4)}
        for name, conf in ordered
    ]


def _apply_postprocessing_rules(
    classification: dict,
    class_names: list[str],
) -> dict:
    """Soft postprocessing: warnings only, no class re-routing.

    Three checks:
      * ``uncertain_healthy`` — top-1 is healthy but confidence/margin
        below thresholds. Surfaces alternatives, no class swap.
      * ``disease_uncertain`` — the v4.1 healthy-fallback fired in
        ``_pick_primary``: top-1 was a disease but margin/threshold was
        weak so the class was downgraded to healthy. We attach a
        warning that names the suspected disease so the UI can show
        "borderline call, ruled out X".
      * ``low_confidence`` — top-1 below the healthy threshold. Lets
        the UI tone down the badge without fighting the model.
    """
    probs = classification["all_probs"]
    top_k = _build_top_k(probs, TOP_K_ALTERNATIVES)
    warnings: list[str] = []

    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    top1_name, top1_prob = ordered[0]
    top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0

    if top1_name == "healthy" and "healthy" in class_names:
        margin = top1_prob - top2_prob
        if top1_prob < HEALTHY_MIN_CONFIDENCE or margin < HEALTHY_MARGIN:
            warnings.append("uncertain_healthy")
            logger.info(
                "healthy gate soft-warning  top1=%.3f  margin=%.3f",
                top1_prob, margin,
            )

    suspected = classification.get("_suspected_disease")
    if suspected:
        warnings.append(f"disease_uncertain:{suspected}")

    if classification.get("low_confidence"):
        warnings.append("low_confidence")

    return {
        "class_name": classification["class_name"],
        "confidence": round(float(classification["confidence"]), 4),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
