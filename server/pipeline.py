"""
PlantVision two-stage houseplant disease detection pipeline.

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``plant_leaf``
                       bboxes (indoor-trained on user/Roboflow Universe
                       indoor leaf crops, not PlantDoc).
Stage 2 (Classifier) — distilled MobileNetV3-Large student (KD ←
                       EfficientNetV2-S teacher) at 256 px. Classifies
                       cropped leaf regions into one of the v2.1
                       trainable classes including ``not_a_plant``.

Behaviour summary:

* **Severity scoring** — each ``analyze`` response carries a
  ``severity: low | medium | high`` derived from (a) the relative bbox
  area of the diagnosed disease, (b) the disease confidence margin
  over ``healthy`` and (c) whether the disease class is degenerative
  (``viral_disease`` always escalates one level — incurable).
* **Mean voting** across valid bboxes (one noisy edge crop can't win
  the room).
* **Size filter** drops tiny detections (<32 px or <0.5 % image area)
  before classification.
* **Healthy fallback** — disease verdicts with weak margin or sub-
  ``DISEASE_MIN_CONFIDENCE`` confidence are downgraded to ``healthy``
  and the suspected disease is surfaced as a warning.
* **Temperature scaling** — when ``temperature.json`` is present, we
  recalibrate probabilities before voting; otherwise T=1.0 (no-op).
* **OOD gating** — combines detector confidence with a **MaxLogit**
  (Hendrycks 2019) score from the classifier. Plain MSP becomes
  unreliable under temperature scaling and label smoothing; MaxLogit
  is robust to both because it operates on the raw logits.

Stage 1 owns localisation only. The disease decision remains 100 % on
the classifier side.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

try:
    from .classifier import DiseaseClassifier  # type: ignore[import-not-found]
    from .conformal import ConformalConfig, load_conformal_config, prediction_set
    from .detector import LeafDetector
except ImportError:
    from classifier import DiseaseClassifier
    from conformal import ConformalConfig, load_conformal_config, prediction_set
    from detector import LeafDetector

logger = logging.getLogger(__name__)

MIN_CROP_SIZE = 32
CROP_PADDING_RATIO = 0.10

# Bboxes smaller than this fraction of the full image area get dropped
# before classification. Real noise bboxes (corner FPs, lighting
# artefacts) are almost always <0.5 % of the image.
MIN_BBOX_AREA_FRAC = 0.005

# Healthy gate — triggers the soft ``uncertain_healthy`` warning when
# top-1 is healthy but weak.
HEALTHY_MIN_CONFIDENCE = 0.75
HEALTHY_MARGIN = 0.10
# Even when top-1 = healthy with confidence ≥ HEALTHY_MIN_CONFIDENCE, if
# the strongest disease has non-trivial mass we still surface an
# ``uncertain_healthy`` warning + the suspected disease in top_k.
HEALTHY_DISEASE_FLOOR = 0.18

# Disease-side gate. If averaged top-1 is a disease but its softmax
# mass is below this threshold, we downgrade to healthy.
DISEASE_MIN_CONFIDENCE = 0.65

# If averaged top-1 is a disease but its margin over ``healthy`` is
# below this, downgrade.
HEALTHY_FALLBACK_MARGIN = 0.15

# NAP (out-of-distribution) gate.
#
# We replaced the historical Maximum-Softmax-Probability (MSP) floor
# with a **MaxLogit** score (Hendrycks 2019) because MSP collapses
# under temperature scaling: at T=0.5 with label_smoothing=0.05, even
# a clear in-distribution sample never crosses MSP=0.45. MaxLogit is
# scale-free w.r.t. temperature and unaffected by label smoothing.
#
# A sample is treated as OOD when BOTH:
#   * the YOLO detector confidence is below ``NAP_DETECTOR_CONF_FLOOR``
#   * the classifier MaxLogit is below ``NAP_MAX_LOGIT_FLOOR``
# The MaxLogit floor is overridable via classes.json.
NAP_DETECTOR_CONF_FLOOR = 0.40
NAP_MAX_LOGIT_FLOOR_DEFAULT = 4.0

# Classes that are not "diseases" — used to compute disease scores
# and to know when to apply the healthy fallback.
_NON_DISEASE = {"healthy", "not_a_plant"}

# Classes that escalate severity by one level on principle. Viral
# infections are incurable and remain a source of spread to neighbours,
# so any high-confidence viral verdict deserves the bump.
_DEGENERATIVE_DISEASES = {"viral_disease"}

# Severity thresholds (relative bbox area). Calibrated against typical
# indoor phone shots: a single leaf at arm's length covers roughly
# 5–15% of the frame; a "the whole plant looks bad" full-frame shot
# covers >30% via NMS-merged bboxes.
SEVERITY_AREA_LOW = 0.06   # below this is "small / localised lesion"
SEVERITY_AREA_HIGH = 0.18  # above this is "widespread"

# Severity thresholds (disease confidence margin over healthy). Small
# margins => the model is hedging => severity stays low even if the
# bbox is large. Large margin => the call is confident => severity
# escalates with area.
SEVERITY_MARGIN_LOW = 0.20
SEVERITY_MARGIN_HIGH = 0.45

TOP_K_ALTERNATIVES = 3

# ── Blur gate ─────────────────────────────────────────────────────────
# Variance-of-Laplacian score below this number means the photo is too
# unfocused to trust the classifier head on. Plant-coloured blur passes
# the YOLO recall floor and the OOD AND-gate (MaxLogit + det_conf both
# need to be low), so without a hard blur check the pipeline cheerfully
# returns "healthy" for a soft-focus brown smear. The 50.0 floor was
# picked from a calibration set of real handheld phone shots: in-focus
# leaves score 200..1500, an obviously-blurry shot scores 5..30. The
# gate is permissive on purpose — sharp-but-low-light photos still
# pass — and outright rejects only the unrecoverable cases.
BLUR_LAPLACIAN_FLOOR = 50.0
# The score is computed on a downscaled grayscale copy so we don't pay
# the cost of a full-resolution convolution per request. 320 px is
# small enough that the Laplacian costs <2 ms on CPU and large enough
# that real focus differences stay separable from sensor noise.
BLUR_DOWNSCALE = 320


def _load_temperature(classifier_path: Path | None) -> float:
    """Look for ``temperature.json`` next to the classifier weights.

    The training notebook writes it automatically. Returns 1.0 (no-op)
    when the file is missing or malformed.
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


def _resolve_max_logit_floor(meta: dict) -> float:
    """Read the OOD MaxLogit floor from classes.json ``ood`` section.

    Contract:
      * dict missing entirely → use the historical default.
      * ``ood.max_logit_floor: <number>`` → use that value.
      * ``ood.max_logit_floor: null`` → MaxLogit gate OFF (return 0.0).
    """
    if not isinstance(meta, dict):
        return NAP_MAX_LOGIT_FLOOR_DEFAULT
    ood = meta.get("ood")
    if not isinstance(ood, dict):
        return NAP_MAX_LOGIT_FLOOR_DEFAULT
    if "max_logit_floor" not in ood:
        return NAP_MAX_LOGIT_FLOOR_DEFAULT
    val = ood.get("max_logit_floor")
    if val is None:
        return 0.0
    try:
        floor = float(val)
    except (TypeError, ValueError):
        return NAP_MAX_LOGIT_FLOOR_DEFAULT
    return max(0.0, floor)


# ── Blur detection ───────────────────────────────────────────────────

# 3x3 Laplacian kernel (4-neighbour discrete approximation of the
# second spatial derivative). Convolving an image with this kernel and
# taking the variance of the result is the canonical, training-free
# focus metric (Pertuz 2013); sharp images produce a wide spread of
# edge responses, smooth/blurry images produce a tight spread.
_LAPLACIAN_KERNEL = np.array(
    [
        [0, 1, 0],
        [1, -4, 1],
        [0, 1, 0],
    ],
    dtype=np.float32,
)


def _variance_of_laplacian(img: Image.Image, max_side: int = BLUR_DOWNSCALE) -> float:
    """Return the variance-of-Laplacian score for ``img``.

    The image is converted to grayscale and downscaled to ``max_side``
    on the long edge first — the absolute score scales with resolution,
    so the threshold needs a stable input size to compare against. A
    plain numpy ``np.gradient``-style 2D convolution would cost ~30 ms
    on a 3 MP frame; the downscale-then-conv path is ~1.5 ms.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return 0.0
    scale = max_side / float(max(w, h))
    if scale < 1.0:
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        scaled = img.resize(new_size, Image.BILINEAR)
    else:
        scaled = img
    gray = np.asarray(scaled.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return 0.0
    # 2-D discrete convolution via separable shifts (faster than
    # scipy.signal.convolve2d for a 3×3 kernel and avoids a SciPy dep).
    centre = gray * _LAPLACIAN_KERNEL[1, 1]
    up = np.roll(gray, -1, axis=0) * _LAPLACIAN_KERNEL[0, 1]
    down = np.roll(gray, 1, axis=0) * _LAPLACIAN_KERNEL[2, 1]
    left = np.roll(gray, -1, axis=1) * _LAPLACIAN_KERNEL[1, 0]
    right = np.roll(gray, 1, axis=1) * _LAPLACIAN_KERNEL[1, 2]
    lap = centre + up + down + left + right
    # Drop the 1-pixel ring where np.roll wrapped the image around.
    inner = lap[1:-1, 1:-1]
    if inner.size == 0:
        return 0.0
    return float(inner.var())


def is_image_too_blurry(img: Image.Image, floor: float = BLUR_LAPLACIAN_FLOOR) -> bool:
    """True when the variance-of-Laplacian is below ``floor``.

    Exposed at module level so the FastAPI layer (and tests) can call
    it without instantiating the full pipeline.
    """
    return _variance_of_laplacian(img) < floor


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

    # Cap the number of bboxes we re-classify per request. Distilled
    # MobileNetV3-L on CPU costs ~10-15 ms per crop in INT8 ONNX, so
    # 4 boxes adds ~40-60 ms — well within the 150 ms budget.
    _MAX_RECLASSIFY = 4

    def __init__(
        self,
        detector_path: Path,
        classifier_path: Path,
    ):
        self.detector = LeafDetector(detector_path)
        self.classifier = DiseaseClassifier(classifier_path)
        self.temperature = _load_temperature(classifier_path)
        meta = getattr(self.classifier, "meta", None) or {}
        self.nap_max_logit_floor = _resolve_max_logit_floor(meta)
        self.conformal: ConformalConfig | None = load_conformal_config(
            classifier_path.parent
        )
        logger.info(
            "Pipeline ready  classifier=%s backend  T=%.3f  "
            "MaxLogit floor=%s  conformal=%s",
            self.classifier.backend,
            self.temperature,
            "off" if self.nap_max_logit_floor <= 0 else f"{self.nap_max_logit_floor:.2f}",
            "off" if self.conformal is None
            else f"{self.conformal.method}@{self.conformal.coverage_target:.0%}",
        )

    # ── main entry point ─────────────────────────────────────────────

    def analyze(self, img: Image.Image) -> dict:
        t0 = time.perf_counter()

        # Blur gate. The classifier was trained on in-focus phone shots
        # and silently hallucinates a plant verdict on soft-focus brown
        # smears — neither the YOLO recall floor nor the
        # detector/MaxLogit OOD AND-gate catches that case. Short-circuit
        # early so the user gets actionable "retake the photo" feedback
        # instead of a confident wrong diagnosis.
        focus_score = _variance_of_laplacian(img)
        if focus_score < BLUR_LAPLACIAN_FLOOR:
            logger.info(
                "Blur gate triggered: var-of-Laplacian=%.1f < %.1f → not_a_plant",
                focus_score, BLUR_LAPLACIAN_FLOOR,
            )
            return self._too_blurry_response(img, focus_score, t0)

        stage1 = self.detector.detect(img)
        detections = stage1["detections"]

        logger.info(
            "Stage 1 → detections=%d  best_conf=%.3f",
            len(detections), stage1["confidence"],
        )

        # No leaf bbox from Stage 1: fall back to classifying the full
        # image and let the classifier's dedicated `not_a_plant` head
        # make the call. This preserves correct labels for wide-frame
        # plant photos that YOLO missed, while still routing genuine
        # non-plant images to NAP via the classifier's own decision.
        if not detections:
            return self._analyze_no_detection(img, t0)

        # Mean voting across valid bboxes (size-filtered).
        primary, classification = self._pick_primary(img, detections)

        # Secondary NAP gate. Even if Stage 1 returned a bbox, a low
        # detector confidence combined with a low classifier MaxLogit
        # is the classic OOD signature: the detector glommed onto a
        # plant-coloured texture and the classifier had no clean answer
        # to give. Route to NAP rather than emitting a guessed disease.
        det_conf = float(stage1.get("confidence", 0.0))
        if self._should_route_to_nap(classification, det_conf):
            classification = self._reroute_to_nap(classification)

        is_diseased = self._is_diseased(classification)

        logger.info(
            "Stage 2 → class=%s  confidence=%.3f  primary_box=%s  voted_over=%d",
            classification["class_name"],
            classification["confidence"],
            primary,
            classification.get("_voted_over", 1),
        )

        final = _apply_postprocessing_rules(classification, self.classifier.CLASS_NAMES)

        severity = compute_severity(
            class_name=final["class_name"],
            confidence=float(final["confidence"]),
            healthy_prob=float(final["all_probs"].get("healthy", 0.0)),
            primary_box=primary,
            image_size=img.size,
            is_diseased=is_diseased,
        )

        return self._build_response(
            final=final,
            severity=severity,
            is_diseased=is_diseased,
            stage1_confidence=stage1["confidence"],
            regions=detections,
            primary_region=primary,
            t0=t0,
        )

    # ── analysis helpers ─────────────────────────────────────────────

    def _too_blurry_response(
        self,
        img: Image.Image,
        focus_score: float,
        t0: float,
    ) -> dict:
        """Build a synthetic ``not_a_plant`` verdict for unfocused input.

        We don't run the classifier when the blur gate fires — there is
        no salvageable signal in the crop. The probs dict assigns the
        full mass to ``not_a_plant`` so downstream metrics, severity
        scoring and toxicity routing all behave as if the classifier
        emitted that verdict directly. A ``blurry_image`` warning is
        added so the client can render a "retake the photo" hint
        instead of a generic NAP message.
        """
        probs = {n: 0.0 for n in self.classifier.CLASS_NAMES}
        if "not_a_plant" in probs:
            probs["not_a_plant"] = 1.0
        else:
            # Defensive: if the classifier was loaded with a class set
            # that doesn't include not_a_plant (shouldn't happen in v2.x,
            # but keep the gate safe), we still surface a low-confidence
            # warning rather than silently classify a blurry crop.
            probs[next(iter(probs))] = 1.0
        final = {
            "class_name": "not_a_plant" if "not_a_plant" in probs else next(iter(probs)),
            "confidence": round(float(probs.get("not_a_plant", 0.0)), 4),
            "all_probs": probs,
            "top_k": [
                {"class": k, "confidence": round(float(v), 4)}
                for k, v in sorted(probs.items(), key=lambda kv: -kv[1])[:TOP_K_ALTERNATIVES]
            ],
            "warnings": ["blurry_image"],
        }
        severity = compute_severity(
            class_name=final["class_name"],
            confidence=float(final["confidence"]),
            healthy_prob=0.0,
            primary_box=None,
            image_size=img.size,
            is_diseased=False,
        )
        response = self._build_response(
            final=final,
            severity=severity,
            is_diseased=False,
            stage1_confidence=0.0,
            regions=[],
            primary_region=None,
            t0=t0,
        )
        # Annotate the response for ops dashboards without breaking the
        # public contract; clients ignore unknown keys.
        response["focus_score"] = round(focus_score, 1)
        return response

    def _analyze_no_detection(self, img: Image.Image, t0: float) -> dict:
        cls_full = self.classifier.classify(img)
        calibrated = _apply_temperature(cls_full["all_probs"], self.temperature)
        classification = {
            **cls_full,
            "all_probs": calibrated,
            "_voted_over": 0,
            "_no_detection": True,
        }
        final = _apply_postprocessing_rules(classification, self.classifier.CLASS_NAMES)
        is_diseased = self._is_diseased(final)
        severity = compute_severity(
            class_name=final["class_name"],
            confidence=float(final["confidence"]),
            healthy_prob=float(final["all_probs"].get("healthy", 0.0)),
            primary_box=None,
            image_size=img.size,
            is_diseased=is_diseased,
        )
        return self._build_response(
            final=final,
            severity=severity,
            is_diseased=is_diseased,
            stage1_confidence=0.0,
            regions=[],
            primary_region=None,
            t0=t0,
        )

    def _build_response(
        self,
        *,
        final: dict,
        severity: str,
        is_diseased: bool,
        stage1_confidence: float,
        regions: list[dict],
        primary_region: Optional[dict],
        t0: float,
    ) -> dict:
        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "severity": severity,
            "prediction_set": self._build_prediction_set(final["all_probs"]),
            "detection": {
                "is_diseased": is_diseased,
                "detector_confidence": stage1_confidence,
                "regions": regions,
                "primary_region": primary_region,
            },
            "elapsed_ms": round(elapsed, 1),
        }

    @staticmethod
    def _is_diseased(classification: dict) -> bool:
        return (
            classification["class_name"] not in _NON_DISEASE
            and classification["confidence"] >= DISEASE_MIN_CONFIDENCE
        )

    def _should_route_to_nap(self, classification: dict, det_conf: float) -> bool:
        """Two-of-two OOD gate. We re-route to ``not_a_plant`` when the
        detector AND the classifier both signal "this is not a leaf":

            det_conf < NAP_DETECTOR_CONF_FLOOR
            AND
            (max_logit < ml_floor)   -- only when ml_floor > 0

        Both floors must independently fire. Either signal alone is too
        noisy: a tight crop of an unfamiliar plant can have low
        MaxLogit even when the detector is confident, and vice versa.

        The historical version short-circuited on detector confidence:
        a single 0.41-confidence false positive bypassed the MaxLogit
        check entirely. We now evaluate both gates explicitly and only
        re-route when both fail.
        """
        cls_name = classification["class_name"]
        if cls_name in _NON_DISEASE:
            return False
        if "not_a_plant" not in self.classifier.CLASS_NAMES:
            return False

        detector_low = det_conf < NAP_DETECTOR_CONF_FLOOR
        ml_floor = self.nap_max_logit_floor
        max_logit = float(classification.get("max_logit", 0.0))
        # MaxLogit gate is "off" when the floor is <= 0 (configurable
        # via classes.json -> ood.max_logit_floor: null). When off, we
        # require only the detector floor to fire — the historical
        # behaviour for backwards compatibility.
        max_logit_low = (ml_floor > 0) and (max_logit < ml_floor)

        if ml_floor > 0:
            triggered = detector_low and max_logit_low
        else:
            triggered = detector_low

        if triggered:
            logger.info(
                "OOD gate triggered (det=%.3f<%.2f, max_logit=%.3f%s) → not_a_plant",
                det_conf, NAP_DETECTOR_CONF_FLOOR,
                max_logit,
                f"<{ml_floor:.2f}" if ml_floor > 0 else " <off>",
            )
        return triggered

    def _build_prediction_set(self, probs: dict[str, float]) -> dict | None:
        """Return the conformal prediction set or ``None`` if unconfigured.

        Honest UX: clients should render the *set* — "leaf_spot_fungal
        OR bacterial_leaf_spot at 90 % coverage" — instead of pretending
        the top-1 was a single confident answer. The dict is keyed by
        ``classes`` / ``coverage`` / ``method`` so the client doesn't
        need to know about the calibration internals.
        """
        cfg = self.conformal
        if cfg is None or not probs:
            return None
        classes = prediction_set(probs, cfg)
        return {
            "classes": classes,
            "coverage": cfg.coverage_target,
            "method": cfg.method,
        }

    def _pick_primary(self, img: Image.Image, detections: list[dict]):
        """Mean-voting primary classification over valid bboxes.

        Returns ``(primary_box, classification_dict)``. The aggregated
        classification dict carries the **maximum** ``max_logit`` across
        the voted crops so the OOD gate sees the strongest in-bbox
        signal we got, not the per-crop average.
        """
        sized = self._size_filter(detections, img.size)
        ranked = sorted(sized, key=lambda d: -d["confidence"])[: self._MAX_RECLASSIFY]
        crops = [self._extract_crop(img, box) for box in ranked]
        cls_results = self.classifier.classify_batch(crops)

        results: list[tuple[dict, dict]] = []
        sum_probs: dict[str, float] = {n: 0.0 for n in self.classifier.CLASS_NAMES}
        max_logit_seen = -float("inf")

        for box, cls_res in zip(ranked, cls_results, strict=True):
            calibrated = _apply_temperature(cls_res["all_probs"], self.temperature)
            results.append((box, {**cls_res, "all_probs": calibrated}))
            for n, p in calibrated.items():
                sum_probs[n] = sum_probs.get(n, 0.0) + float(p)
            ml = float(cls_res.get("max_logit", 0.0))
            if ml > max_logit_seen:
                max_logit_seen = ml

        n_boxes = max(len(results), 1)
        mean_probs = {name: total / n_boxes for name, total in sum_probs.items()}

        top1_class = max(mean_probs, key=mean_probs.get)
        top1_conf = float(mean_probs[top1_class])

        downgrade = _maybe_apply_healthy_fallback(mean_probs, top1_class, top1_conf)
        if downgrade is not None:
            mean_probs = downgrade["mean_probs"]
            top1_class = "healthy"
            top1_conf = downgrade["top1_conf"]
            suspected_disease: Optional[str] = downgrade["suspected"]
        else:
            suspected_disease = None

        primary_box = self._select_primary_box(
            results=results,
            top1_class=top1_class,
            downgraded=downgrade is not None,
        )

        aggregated_cls = {
            "class_name": top1_class,
            "confidence": top1_conf,
            "all_probs": mean_probs,
            "max_logit": max_logit_seen if max_logit_seen != -float("inf") else 0.0,
            "low_confidence": top1_conf < HEALTHY_MIN_CONFIDENCE,
            "_voted_over": n_boxes,
            "_suspected_disease": suspected_disease,
        }
        return primary_box, aggregated_cls

    @staticmethod
    def _size_filter(detections: list[dict], image_size: tuple[int, int]) -> list[dict]:
        """Drop tiny bboxes; if everything filters out, keep the largest."""
        W, H = image_size
        img_area = max(W * H, 1)
        sized = [
            d for d in detections
            if int(d.get("width", 0)) >= MIN_CROP_SIZE
            and int(d.get("height", 0)) >= MIN_CROP_SIZE
            and (int(d.get("width", 0)) * int(d.get("height", 0))) / img_area
                >= MIN_BBOX_AREA_FRAC
        ]
        if sized:
            return sized
        return [
            max(
                detections,
                key=lambda d: int(d.get("width", 0)) * int(d.get("height", 0)),
            )
        ]

    @staticmethod
    def _select_primary_box(
        *,
        results: list[tuple[dict, dict]],
        top1_class: str,
        downgraded: bool,
    ) -> dict:
        """Prefer a bbox whose own top-1 matches the aggregated decision."""
        matching = [
            (box, cls) for box, cls in results
            if cls.get("class_name") == top1_class or (
                downgraded and top1_class == "healthy"
                and max(cls["all_probs"], key=cls["all_probs"].get) == "healthy"
            )
        ]
        if matching:
            return max(
                matching,
                key=lambda r: float(r[1]["all_probs"].get(top1_class, 0.0)),
            )[0]
        return max(
            results,
            key=lambda r: int(r[0].get("width", 0)) * int(r[0].get("height", 0)),
        )[0]

    def _reroute_to_nap(self, classification: dict) -> dict:
        """Rewrite an OOD-gated classification to a confident NAP verdict.

        We don't invent confidence — we report ``1 - cls_msp`` (i.e. how
        confident we are it is NOT the suspected disease), bounded so
        the UI doesn't claim 100 % certainty about negativity either.
        Probability mass is moved into ``not_a_plant`` and the rest is
        renormalised so all_probs/top_k still sum to 1.
        """
        names = list(classification.get("all_probs", {}).keys()) or self.classifier.CLASS_NAMES
        msp = float(classification.get("confidence", 0.0))
        nap_conf = max(min(1.0 - msp, 0.95), 0.55)
        probs = {n: 0.0 for n in names}
        probs["not_a_plant"] = nap_conf
        leftover = 1.0 - nap_conf
        original = classification.get("all_probs") or {}
        residual_total = sum(
            v for k, v in original.items() if k != "not_a_plant"
        )
        if residual_total > 0 and leftover > 0:
            for k, v in original.items():
                if k == "not_a_plant":
                    continue
                probs[k] = float(v) * (leftover / residual_total)
        return {
            "class_name": "not_a_plant",
            "confidence": nap_conf,
            "all_probs": probs,
            "max_logit": float(classification.get("max_logit", 0.0)),
            "low_confidence": False,
            "_voted_over": classification.get("_voted_over", 1),
            "_suspected_disease": None,
            "_ood_gated": True,
        }

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


def _maybe_apply_healthy_fallback(
    mean_probs: dict[str, float],
    top1_class: str,
    top1_conf: float,
) -> dict | None:
    """Downgrade weak disease verdicts to ``healthy``.

    Returns the rewritten state on downgrade, or ``None`` to keep the
    original verdict. Mutates the caller's ``mean_probs`` only when
    downgrading.
    """
    if top1_class in _NON_DISEASE or "healthy" not in mean_probs:
        return None

    healthy_p = float(mean_probs.get("healthy", 0.0))
    margin = top1_conf - healthy_p

    if margin >= HEALTHY_FALLBACK_MARGIN and top1_conf >= DISEASE_MIN_CONFIDENCE:
        return None

    reason = (
        f"margin={margin:.3f}<{HEALTHY_FALLBACK_MARGIN}"
        if margin < HEALTHY_FALLBACK_MARGIN
        else f"threshold={top1_conf:.3f}<{DISEASE_MIN_CONFIDENCE}"
    )
    inverted_conf = min(max(healthy_p, 1.0 - top1_conf), 0.95)
    logger.info(
        "Healthy fallback (%s)  was=%s/%.3f  → healthy/%.3f",
        reason, top1_class, top1_conf, inverted_conf,
    )

    target_healthy = max(inverted_conf, healthy_p)
    delta = min(top1_conf - inverted_conf, mean_probs[top1_class])
    if delta > 0:
        mean_probs[top1_class] -= delta
        mean_probs["healthy"] = mean_probs.get("healthy", 0.0) + delta

    total = sum(mean_probs.values())
    if total > 0:
        mean_probs = {n: p / total for n, p in mean_probs.items()}

    new_conf = float(mean_probs["healthy"])
    if new_conf < target_healthy:
        new_conf = target_healthy

    return {
        "mean_probs": mean_probs,
        "top1_conf": new_conf,
        "suspected": top1_class,
    }


def compute_severity(
    *,
    class_name: str,
    confidence: float,
    healthy_prob: float,
    primary_box: Optional[dict],
    image_size: tuple[int, int],
    is_diseased: bool,
) -> str:
    """Heuristic severity scoring (low / medium / high)."""
    if not is_diseased or class_name in _NON_DISEASE:
        return "low"

    W, H = image_size
    img_area = max(W * H, 1)
    if primary_box:
        w = float(primary_box.get("width", 0))
        h = float(primary_box.get("height", 0))
        area_frac = max(0.0, min(1.0, (w * h) / img_area))
    else:
        area_frac = 0.0

    margin = max(0.0, float(confidence) - float(healthy_prob))

    if area_frac >= SEVERITY_AREA_HIGH:
        area_pts = 3
    elif area_frac >= SEVERITY_AREA_LOW:
        area_pts = 2
    else:
        area_pts = 1

    if margin >= SEVERITY_MARGIN_HIGH:
        margin_pts = 3
    elif margin >= SEVERITY_MARGIN_LOW:
        margin_pts = 2
    else:
        margin_pts = 1

    score = area_pts + margin_pts
    if class_name in _DEGENERATIVE_DISEASES:
        score += 1

    if score >= 6:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _apply_postprocessing_rules(
    classification: dict,
    class_names: list[str],
) -> dict:
    """Soft postprocessing: warnings only, no class re-routing.

    Three checks:
      * ``uncertain_healthy`` — top-1 is healthy but confidence/margin
        below thresholds. Surfaces alternatives, no class swap.
      * ``disease_uncertain`` — the healthy-fallback fired in
        ``_pick_primary``: top-1 was a disease but margin/threshold was
        weak so the class was downgraded to healthy.
      * ``low_confidence`` — top-1 below the healthy threshold.
    """
    probs = classification["all_probs"]
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    top_k = [
        {"class": name, "confidence": round(float(conf), 4)}
        for name, conf in ordered[:TOP_K_ALTERNATIVES]
    ]
    warnings: list[str] = []

    top1_name, top1_prob = ordered[0]
    top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0

    if top1_name == "healthy" and "healthy" in class_names:
        margin = top1_prob - top2_prob
        worst_disease = max(
            ((n, p) for n, p in probs.items() if n not in _NON_DISEASE),
            key=lambda kv: kv[1],
            default=(None, 0.0),
        )
        weak = top1_prob < HEALTHY_MIN_CONFIDENCE or margin < HEALTHY_MARGIN
        disease_floor_hit = worst_disease[1] >= HEALTHY_DISEASE_FLOOR
        if weak or disease_floor_hit:
            warnings.append("uncertain_healthy")
            if disease_floor_hit and worst_disease[0]:
                warnings.append(f"disease_suspected:{worst_disease[0]}")
            logger.info(
                "healthy gate soft-warning  top1=%.3f  margin=%.3f  worst_disease=%s/%.3f",
                top1_prob, margin, worst_disease[0], worst_disease[1],
            )

    suspected = classification.get("_suspected_disease")
    if suspected:
        warnings.append(f"disease_uncertain:{suspected}")

    if classification.get("_ood_gated"):
        warnings.append("nap_ood_gated")

    if classification.get("low_confidence"):
        warnings.append("low_confidence")

    return {
        "class_name": classification["class_name"],
        "confidence": round(float(classification["confidence"]), 4),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
