"""
PlantVision two-stage houseplant disease detection pipeline.

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``leaf`` bboxes.
Stage 2 (Classifier) — EfficientNet-B0 (ONNX). Classifies cropped leaf
                       regions into one of up to 10 houseplant
                       condition classes including ``not_a_plant``.

Behaviour summary:

* **Severity scoring** — each ``analyze`` response carries a
  ``severity: low | medium | high`` derived from (a) the relative bbox
  area of the diagnosed disease, (b) the disease confidence margin
  over ``healthy`` and (c) whether the disease class is degenerative
  (``late_blight``, ``mosaic_virus`` always escalate one level).
* **TTA at inference** — horizontal-flip averaging is applied to the
  classifier output. Roughly +1 % top-1 vs cold inference.
* **Mean voting** across valid bboxes (one noisy edge crop can't win
  the room).
* **Size filter** drops tiny detections (<32 px or <0.5 % image area)
  before classification.
* **Healthy fallback** — disease verdicts with weak margin or sub-
  ``DISEASE_MIN_CONFIDENCE`` confidence are downgraded to ``healthy``
  and the suspected disease is surfaced as a warning.
* **Temperature scaling** — when ``temperature.json`` is present, we
  recalibrate probabilities before voting; otherwise T=1.0 (no-op).

Stage 1 owns localisation only. The disease decision remains 100 % on
the classifier side.
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

# Bboxes smaller than this fraction of the full image area get dropped
# before classification. Real noise bboxes (corner FPs, lighting
# artefacts) are almost always <0.5 % of the image.
MIN_BBOX_AREA_FRAC = 0.005

# Healthy gate — triggers the soft ``uncertain_healthy`` warning when
# top-1 is healthy but weak. Bumped 0.55 → 0.75 after the live-data
# eval found 79 % of sick leaves classified as healthy, often at high
# confidence; tighter floor + disease-mass floor below catch most of
# those without nuking healthy recall on truly clean photos.
HEALTHY_MIN_CONFIDENCE = 0.75
HEALTHY_MARGIN = 0.10
# Even when top-1 = healthy with confidence ≥ HEALTHY_MIN_CONFIDENCE, if
# the strongest disease has non-trivial mass we still surface an
# ``uncertain_healthy`` warning + the suspected disease in top_k. Live
# eval showed sunburn → healthy 0.78 with anthracnose ≈ 0.18 — a hard
# threshold misses these, a per-disease mass floor catches them.
HEALTHY_DISEASE_FLOOR = 0.18

# Disease-side gate. If averaged top-1 is a disease but its softmax
# mass is below this threshold, we downgrade to healthy.
DISEASE_MIN_CONFIDENCE = 0.65

# If averaged top-1 is a disease but its margin over ``healthy`` is
# below this, downgrade.
HEALTHY_FALLBACK_MARGIN = 0.15

# NAP (out-of-distribution) gate. When the detector confidence is below
# this AND the classifier's max-softmax-probability (MSP) is also weak,
# we route to ``not_a_plant`` instead of guessing a disease label.
NAP_DETECTOR_CONF_FLOOR = 0.40
NAP_CLASSIFIER_MSP_FLOOR = 0.45

# Classes that are not "diseases" — used to compute disease scores
# and to know when to apply the healthy fallback.
_NON_DISEASE = {"healthy", "not_a_plant"}

# Classes whose training relied on visual proxies from non-houseplant
# datasets (PV field crops, strawberry leaf scorch as sunburn proxy,
# bacterial spot from tomato/pepper). Live eval shows F1≈0 on these —
# we surface a warning so the UI can mark verdicts as experimental.
_PROXY_TRAINED = {"bacterial_leaf_spot", "late_blight", "sunburn"}

# Classes that escalate severity by one level on principle. They have
# aggressive systemic spread (mosaic virus is incurable; late blight can
# wipe out a plant in days under indoor humidity).
_DEGENERATIVE_DISEASES = {"late_blight", "mosaic_virus"}

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
        # Opt-in temperature scaling. Defaults to 1.0 (no-op) if
        # temperature.json is absent.
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
                "severity": "low",
                "detection": {
                    "is_diseased": False,
                    "detector_confidence": 0.0,
                    "regions": [],
                    "primary_region": None,
                },
                "pipeline_mode": self.mode,
                "elapsed_ms": round(elapsed, 1),
            }

        # Mean voting across valid bboxes (size-filtered).
        primary, classification = self._pick_primary(img, detections)

        # Secondary NAP gate. Even if Stage 1 returned a bbox, a low
        # detector confidence combined with a low classifier max-softmax
        # is the classic OOD signature: the detector glommed onto a
        # plant-coloured texture and the classifier had no clean answer
        # to give. Route to NAP rather than emitting a guessed disease.
        # We only override when the classifier *isn't* already saying NAP
        # (no double-negative routing) and isn't confident in healthy
        # (a confident healthy verdict is its own valid signal).
        det_conf = float(stage1.get("confidence", 0.0))
        cls_msp = float(classification.get("confidence", 0.0))
        cls_name = classification["class_name"]
        if (
            cls_name not in _NON_DISEASE
            and det_conf < NAP_DETECTOR_CONF_FLOOR
            and cls_msp < NAP_CLASSIFIER_MSP_FLOOR
            and "not_a_plant" in self.classifier.CLASS_NAMES
        ):
            logger.info(
                "OOD gate triggered (det=%.3f<%.2f, msp=%.3f<%.2f) → not_a_plant",
                det_conf, NAP_DETECTOR_CONF_FLOOR,
                cls_msp, NAP_CLASSIFIER_MSP_FLOOR,
            )
            classification = self._reroute_to_nap(classification)

        # ``is_diseased`` shares the disease verdict's confidence
        # threshold so the detector overlay and the verdict never
        # disagree.
        is_diseased = (
            classification["class_name"] not in _NON_DISEASE
            and classification["confidence"] >= DISEASE_MIN_CONFIDENCE
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

        # Severity scoring. We compute it on the *post-fallback* class
        # so the UI severity always agrees with the displayed diagnosis
        # (a downgrade-to-healthy ⇒ severity=low).
        severity = compute_severity(
            class_name=final["class_name"],
            confidence=float(final["confidence"]),
            healthy_prob=float(final["all_probs"].get("healthy", 0.0)),
            primary_box=primary,
            image_size=img.size,
            is_diseased=is_diseased,
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "severity": severity,
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
        """Mean-voting primary classification over valid bboxes.

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

        # 3. Classify all bboxes in a single batched forward pass.
        # Cuts the per-call setup cost on CPU — the dominant overhead
        # for ≤4 boxes — without changing numerics.
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
        # margin over healthy is small, downgrade.
        #
        # Confidence semantics on fallback: we don't surface the raw
        # ``healthy`` probability — for an overconfident model that's
        # often 3-5 %, which reads to the user as "97 % sick" even
        # though the actual signal is "we're not confident enough to
        # claim a disease". Instead we report ``1 - top1_disease_conf``
        # — clamped to be at least the raw healthy prob (we never lie
        # downward) and at most 0.95 (we don't claim certainty we never
        # had).
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
        # Spread the rest over the remaining classes proportional to
        # the original distribution (so e.g. a "healthy" runner-up
        # still ranks above other diseases in the alternatives list).
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

def compute_severity(
    *,
    class_name: str,
    confidence: float,
    healthy_prob: float,
    primary_box: Optional[dict],
    image_size: tuple[int, int],
    is_diseased: bool,
) -> str:
    """Heuristic severity scoring (low / medium / high).

    Inputs combine three independent signals:
      1. Relative bbox area — how much of the frame is the lesion
         occupying. Tiny localised spots stay "low" even at high
         confidence; full-leaf coverage escalates.
      2. Confidence margin over healthy — how decisive the model is
         about it being a disease at all. Hedged calls cap at "low".
      3. Class-level prior for degenerative diseases (late_blight,
         mosaic_virus) which always step up one level.

    Returns ``low`` for healthy / NAP / non-diseased verdicts.
    """
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

    # Score in [0, 6] from area + margin, then bucket. Each axis worth
    # up to 3 points so neither one alone can pin the result.
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
      * ``disease_uncertain`` — the healthy-fallback fired in
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
        # Strongest disease (excludes healthy / not_a_plant from the floor).
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

    # Proxy-trained classes carry structurally lower precision on
    # houseplants — flag them whenever they win.
    if top1_name in _PROXY_TRAINED:
        warnings.append(f"proxy_trained:{top1_name}")

    if classification.get("_ood_gated"):
        # Helps the UI explain "we routed this to Not-a-Plant because the
        # detector + classifier signals were both weak" rather than the
        # default "no leaves found" explanation.
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
