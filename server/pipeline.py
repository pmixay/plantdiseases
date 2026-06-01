"""
PlantVision two-stage houseplant disease detection pipeline.

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``plant_leaf``
                       bboxes (indoor-trained on user/Roboflow Universe
                       indoor leaf crops, not PlantDoc).
Stage 2 (Classifier) — distilled MobileNetV3-Large student (KD ←
                       EfficientNetV2-S teacher) at 256 px. Classifies
                       cropped leaf regions into one of the v2.2
                       trainable classes including ``not_a_plant``.
                       The runtime reads ``models/classes.json`` at
                       boot so the actual class set is whatever the
                       shipped artefact declares.

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
* **Weak-healthy reroute** — when top-1 is ``healthy`` but the
  margin to the strongest disease is too narrow, we surface the
  disease instead so the UI doesn't reassure on an obviously-sick
  plant. (Symmetric "weak-disease → healthy" downgrade was removed
  in favour of an honest low-confidence flag — see the comment in
  ``_pick_primary``.)
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
HEALTHY_DISEASE_FLOOR = 0.12

# Hard reroute — when top-1 = healthy but the confidence is below this
# floor, the model is essentially guessing. We swap the verdict to the
# strongest disease class so the UI doesn't reassure the user about an
# obviously-unhealthy plant. Companion rule: if there's no disease
# alternative meeting ``HEALTHY_DISEASE_FLOOR`` we keep ``healthy`` but
# attach the ``low_confidence`` warning so the UI demotes the verdict.
#
# Tightened from 0.50 → 0.65 after a feedback-screenshot review where
# the model was happily emitting "Healthy 55–61 %" on visibly dead
# / dried plants (the head doesn't carry leaf-scorch / nutrient-
# deficiency yet, so damage signal collapses to the weakest healthy).
# 0.65 is the empirical knee — below it the in-frame disease class
# is reliable enough to swap to; above it we leave the verdict alone
# and let the soft ``uncertain_healthy`` warning carry the demotion.
HEALTHY_REROUTE_FLOOR = 0.65

# Second healthy-reroute gate, this time keyed on the *margin* between
# healthy and the strongest disease class rather than the absolute
# confidence. Caught a class of errors the absolute floor missed:
# top1=healthy at e.g. 0.55 with disease at 0.35 is essentially a
# coin flip but slips past ``HEALTHY_REROUTE_FLOOR``. Threshold
# pulled from feedback-row analysis: error rows had next-diseased
# gaps of 0.06 / 0.20 / 0.42; <0.30 catches the 0.06 and 0.20 cases.
# Companion ``HEALTHY_GAP_DISEASE_FLOOR`` keeps the gate from firing
# when the strongest disease is itself just noise (e.g. 0.05).
HEALTHY_GAP_MARGIN = 0.30
HEALTHY_GAP_DISEASE_FLOOR = 0.10

# Stricter disease floor for the *absolute_hit* reroute path. The 0.12
# ``HEALTHY_DISEASE_FLOOR`` was designed for the soft ``uncertain_healthy``
# warning that surfaces a suspected disease next to a confident healthy
# verdict; reusing it for the hard reroute let a 12 % gray_mold trump a
# 54 % healthy and produced a 12 %-confident disease verdict that the
# UI then had to demote. The gap_hit path keeps its own floor so the
# 0.29 / 0.24 "visibly dead plant" screenshot regression still reroutes
# via the margin trigger.
HEALTHY_REROUTE_DISEASE_FLOOR = 0.25

# Very-low-confidence floor — applies to ANY verdict (healthy or
# disease) and is purely a UI-demotion signal. When top-1 commits
# below this floor the response gains a ``very_low_confidence``
# warning so the Android result screen can render a prominent
# "low confidence — please retake the photo" banner instead of
# showing a confident-looking 31 %-confidence disease badge. Driven
# by a feedback-screenshot where a meme image got Gray-Mold@31 %.
VERY_LOW_CONFIDENCE_FLOOR = 0.40

# Disease-side gate. If averaged top-1 is a disease but its softmax
# mass is below this threshold, we gate community-upload eligibility
# and severity escalation via ``_is_diseased``. (The disease verdict
# itself is no longer downgraded to healthy below this floor — the
# legacy fallback was removed because it manufactured fake-healthy
# distributions; see the comment block in ``_pick_primary``.)
DISEASE_MIN_CONFIDENCE = 0.65

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
        if self.nap_max_logit_floor <= 0:
            logger.warning(
                "OOD MaxLogit gate is OFF on this model — separation_p50 in "
                "classes.json showed NAP medians above plant medians, so the "
                "AND-gate degrades to detector-confidence-only. NAP rejection "
                "rests on the classifier's own not_a_plant head until a "
                "different OOD score is calibrated.",
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
            image_size=img.size,
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
            image_size=img.size,
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
            image_size=img.size,
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
        primary_region: dict | None,
        image_size: tuple[int, int],
        t0: float,
    ) -> dict:
        elapsed = (time.perf_counter() - t0) * 1000
        img_w, img_h = image_size
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "severity": severity,
            "prediction_set": self._build_prediction_set(
                final["all_probs"], final["class_name"]
            ),
            "detection": {
                "is_diseased": is_diseased,
                "detector_confidence": stage1_confidence,
                # ``image_width`` / ``image_height`` are the pixel dims of
                # the image the detector saw — i.e., the upload after
                # PIL decode. Clients render bboxes against these dims,
                # not against the raw camera file (which may be larger
                # and EXIF-rotated in the opposite axis).
                "image_width": img_w,
                "image_height": img_h,
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

        triggered = (
            (detector_low and max_logit_low) if ml_floor > 0 else detector_low
        )

        if triggered:
            logger.info(
                "OOD gate triggered (det=%.3f<%.2f, max_logit=%.3f%s) → not_a_plant",
                det_conf, NAP_DETECTOR_CONF_FLOOR,
                max_logit,
                f"<{ml_floor:.2f}" if ml_floor > 0 else " <off>",
            )
        return triggered

    def _build_prediction_set(
        self, probs: dict[str, float], class_name: str | None = None
    ) -> dict | None:
        """Return the conformal prediction set or ``None`` if unconfigured.

        Honest UX: clients should render the *set* — "leaf_spot
        OR viral_disease at 90 % coverage" — instead of pretending
        the top-1 was a single confident answer. The dict is keyed by
        ``classes`` / ``coverage`` / ``method`` so the client doesn't
        need to know about the calibration internals.

        The conformal calibration covers *plant* inputs only — the
        ``not_a_plant`` verdict is reached through the detector + OOD
        gate, not RAPS, so attaching a coverage-guaranteed set to a NAP
        response would over-claim. Suppress it for those cases.
        """
        cfg = self.conformal
        if cfg is None or not probs:
            return None
        if class_name == "not_a_plant":
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

        # The legacy ``_maybe_apply_healthy_fallback`` used to flip a
        # weak disease verdict to healthy by transferring the rejected
        # disease's softmax mass onto healthy. That synthesised a fake
        # "healthy ~0.51" distribution out of a real disease
        # prediction, gated off the weak-healthy reroute via the
        # ``_suspected_disease`` flag, and surfaced two contradictory
        # disease names in the warnings (the killed-by-fallback class
        # and the worst-after-fallback class). It was the dominant
        # source of "user-sees-healthy-but-photo-is-clearly-sick"
        # errors in the feedback corpus. Removed entirely — weak
        # disease verdicts now stay disease verdicts and carry
        # ``low_confidence`` for the UI to demote honestly. The
        # symmetric "weak healthy with a disease alternative" case is
        # still caught by the gap reroute in
        # ``_apply_postprocessing_rules``.

        primary_box = self._select_primary_box(
            results=results,
            top1_class=top1_class,
            downgraded=False,
        )

        aggregated_cls = {
            "class_name": top1_class,
            "confidence": top1_conf,
            "all_probs": mean_probs,
            "max_logit": max_logit_seen if max_logit_seen != -float("inf") else 0.0,
            "low_confidence": top1_conf < HEALTHY_MIN_CONFIDENCE,
            "_voted_over": n_boxes,
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
        """Pick the bbox most worth highlighting on the result screen.

        Heuristic: rank every region by ``1 − P(healthy)`` (i.e. its
        own per-crop probability of being NOT healthy) and pick the
        worst-looking one. This surfaces the suspicious region rather
        than the most-confident-healthy leaf, which matters most when
        the aggregated verdict is itself a disease — the user expects
        to see the diseased leaf called out, not the picture-perfect
        one beside it. Even on a confidently-healthy aggregate it's a
        better default: if any single region looks sick, showing it is
        more informative than centring on the showiest healthy leaf.

        Tie-breakers (in order): higher per-crop confidence for the
        aggregated ``top1_class``, then larger area — same fallbacks
        the old "matches top-1" path used.
        """
        if not results:
            raise ValueError("_select_primary_box called with no regions")

        def disease_score(crop_cls: dict) -> float:
            return 1.0 - float(crop_cls["all_probs"].get("healthy", 0.0))

        def top1_match(crop_cls: dict) -> float:
            return float(crop_cls["all_probs"].get(top1_class, 0.0))

        def area(box: dict) -> int:
            return int(box.get("width", 0)) * int(box.get("height", 0))

        ranked = sorted(
            results,
            key=lambda r: (disease_score(r[1]), top1_match(r[1]), area(r[0])),
            reverse=True,
        )
        # ``downgraded`` is kept in the signature for call-site
        # compatibility; the new ranking already does the right thing
        # in the downgrade case (the most-diseased region still wins
        # even when the aggregate flipped to healthy).
        _ = downgraded
        return ranked[0][0]

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
            "_ood_gated": True,
        }

    @staticmethod
    def _extract_crop(img: Image.Image, region: dict | None) -> Image.Image:
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


def compute_severity(
    *,
    class_name: str,
    confidence: float,
    healthy_prob: float,
    primary_box: dict | None,
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
    """Soft postprocessing + weak-healthy reroute.

    Checks, in order:
      * **weak-healthy reroute** — top-1 = healthy but confidence is
        below ``HEALTHY_REROUTE_FLOOR`` AND a disease class meets
        ``HEALTHY_DISEASE_FLOOR``. We swap the verdict to the
        strongest disease so the UI doesn't say "Healthy" on what is
        plainly a sick plant. Attaches a ``weak_healthy_rerouted``
        warning so the client can render the demotion explicitly.
      * ``uncertain_healthy`` — top-1 is healthy, no disease above the
        reroute threshold, but confidence/margin still below the soft
        thresholds. Surfaces alternatives, no class swap.
      * ``low_confidence`` — top-1 below the healthy threshold.
    """
    probs = classification["all_probs"]
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    warnings: list[str] = []

    top1_name, top1_prob = ordered[0]
    top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0

    # ── Weak-healthy reroute ────────────────────────────────────────
    # When the model labels something as healthy with sub-50% confidence
    # while a real disease class has any meaningful mass, "healthy" is
    # almost certainly wrong. Reroute the verdict so the UI surfaces
    # the more likely call.
    if top1_name == "healthy" and "healthy" in class_names:
        worst_disease = max(
            ((n, p) for n, p in probs.items() if n not in _NON_DISEASE),
            key=lambda kv: kv[1],
            default=(None, 0.0),
        )
        # Two parallel triggers, either of which is enough on its own:
        #
        #   * absolute floor — top1 confidence < HEALTHY_REROUTE_FLOOR
        #     (0.65) with the strongest disease at or above
        #     HEALTHY_REROUTE_DISEASE_FLOOR (0.25). The 0.25 floor blocks
        #     reroutes onto sub-quarter-confidence noise: a 0.54 healthy
        #     vs 0.12 disease should stay healthy, with the soft
        #     `disease_suspected` warning carrying the alert.
        #   * gap floor — healthy is winning but only by < GAP_MARGIN
        #     (0.30) over the strongest disease, and that disease has
        #     at least GAP_DISEASE_FLOOR (0.10) mass.
        #
        # The gap floor catches the "healthy 0.29 / leaf_spot 0.24"
        # visibly-dead-plant screenshot where the model is essentially
        # a coin flip and the absolute floor's 0.25 disease threshold
        # is intentionally just out of reach.
        absolute_hit = (
            top1_prob < HEALTHY_REROUTE_FLOOR
            and worst_disease[1] >= HEALTHY_REROUTE_DISEASE_FLOOR
        )
        gap = top1_prob - worst_disease[1] if worst_disease[0] is not None else float("inf")
        gap_hit = (
            worst_disease[0] is not None
            and worst_disease[1] >= HEALTHY_GAP_DISEASE_FLOOR
            and gap < HEALTHY_GAP_MARGIN
        )
        if worst_disease[0] is not None and (absolute_hit or gap_hit):
            logger.info(
                "Weak-healthy reroute  was=healthy/%.3f  → %s/%.3f  "
                "trigger=%s  gap=%.3f",
                top1_prob, worst_disease[0], worst_disease[1],
                "absolute" if absolute_hit else "gap",
                gap,
            )
            classification = {
                **classification,
                "class_name": worst_disease[0],
                "confidence": float(worst_disease[1]),
            }
            warnings.append("weak_healthy_rerouted")
            # Re-sort the probability list around the new top-1 so the
            # rest of this function (top_k construction, healthy-gate
            # warning) sees the corrected verdict.
            ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
            ordered = sorted(
                ordered, key=lambda kv: (kv[0] != worst_disease[0], -kv[1]),
            )
            top1_name, top1_prob = ordered[0]
            top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0

    top_k = [
        {"class": name, "confidence": round(float(conf), 4)}
        for name, conf in ordered[:TOP_K_ALTERNATIVES]
    ]

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

    if classification.get("_ood_gated"):
        warnings.append("nap_ood_gated")

    if classification.get("low_confidence"):
        warnings.append("low_confidence")

    # Very-low-confidence floor — purely a UI hint, no class swap.
    # The post-reroute confidence is what the user sees, so we check
    # ``classification["confidence"]`` rather than the original
    # ``top1_prob`` (which may have been a 0.51-healthy that got
    # rerouted to a 0.32-disease above). Any verdict that lands below
    # the floor — including a disease that the model emitted at
    # 31 % on a junk image — earns the warning so the UI can render
    # a "please retake the photo" banner.
    if float(classification["confidence"]) < VERY_LOW_CONFIDENCE_FLOOR:
        warnings.append("very_low_confidence")

    return {
        "class_name": classification["class_name"],
        "confidence": round(float(classification["confidence"]), 4),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
