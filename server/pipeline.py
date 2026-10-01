"""
PlantVision two-stage plant disease detection pipeline.

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``plant_leaf``
                       bboxes (indoor-trained on user/Roboflow Universe
                       indoor leaf crops, not PlantDoc).
Stage 2 (Classifier) — frozen DINOv2 ViT-S/14 backbone + LoRA on the
                       later blocks + a trained MLP head at 224 px
                       (architecture-neutral, ONNX-only runtime — there is
                       no PyTorch/.pth fallback). Classifies cropped leaf
                       regions into one of the trainable classes
                       including ``not_a_plant``.
                       The runtime reads ``models/classes.json`` at
                       boot so the actual class set is whatever the
                       shipped artefact declares.

Behaviour summary:

* **Severity contract** — the current models classify a condition and the
  detector localises a leaf; neither predicts lesion extent.  ``severity``
  therefore stays conservatively ``low`` until a separately labelled severity
  target exists.  In particular, leaf-box area is never presented as disease
  area.
* **Mean voting** across valid bboxes (one noisy edge crop can't win
  the room).
* **Size filter** drops tiny detections (<32 px or <0.5 % image area)
  before classification.
* **Probability-preserving uncertainty** — postprocessing never replaces the
  classifier argmax with a lower-probability class.  A weak ``healthy`` result
  remains healthy while ``top_k`` plus ``uncertain_healthy`` /
  ``disease_suspected`` surface the competing disease.  Weak disease results
  likewise keep their measured class and gain confidence warnings.
* **Temperature scaling** — when ``temperature.json`` is present, we
  recalibrate probabilities before voting; otherwise T=1.0 (no-op).
* **OOD gating** — a three-of-three gate: low detector confidence AND a
  firing classifier-side OOD signal AND no veto from the trained
  ``not_a_plant`` head. The classifier-side signal is an **embedding-space
  k-NN distance** on the DINOv2 CLS vector (``ood.py``) when the bundle
  ships a bank, and falls back to a **MaxLogit** (Hendrycks 2019) floor
  when it does not. A gate hit is an abstention warning, not a synthetic
  ``not_a_plant`` probability: the calibrated classifier distribution remains
  unchanged. Plain MSP is unusable for either role: it collapses under
  temperature scaling and label smoothing. See ``_should_route_to_nap`` for
  the full contract.

Stage 1 owns localisation only. The disease decision remains 100 % on
the classifier side.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from .bundle import BundleLoadError, VerifiedBundle, verify_bundle
    from .classifier import DiseaseClassifier  # type: ignore[import-not-found]
    from .config import get_bool
    from .conformal import ConformalConfig, load_conformal_config, prediction_set
    from .detector import LeafDetector
    from .ood import EmbeddingOODScorer, load_embedding_ood
except ImportError:
    from bundle import BundleLoadError, VerifiedBundle, verify_bundle
    from classifier import DiseaseClassifier
    from config import get_bool
    from conformal import ConformalConfig, load_conformal_config, prediction_set
    from detector import LeafDetector
    from ood import EmbeddingOODScorer, load_embedding_ood

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
# via ``_is_diseased``. (The disease verdict
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
# A sample is treated as OOD when ALL of:
#   * the YOLO detector confidence is below ``NAP_DETECTOR_CONF_FLOOR``
#   * the classifier MaxLogit is below ``NAP_MAX_LOGIT_FLOOR``
#   * the classifier's OWN ``not_a_plant`` head is not vetoing (see below)
# The MaxLogit floor is overridable via classes.json.
#
# ``NAP_HEAD_MASS_FLOOR`` is a veto: an auxiliary distance/logit heuristic must
# not overrule a classifier head that says the frame is a plant. Historical
# measurements previously quoted here came from the now-invalid private v2
# regression instrument and are not release evidence. Strict production
# bundles must declare the value explicitly, and the entire secondary overlay
# remains off unless a matching full-served-pipeline calibration is present.
#
# ``ood.max_logit_scope`` selects WHICH max the floor is compared against:
#   * ``"all_classes"`` (default, legacy bundles) — max over every logit.
#   * ``"plant_only"``  — max over the logits EXCLUDING ``not_a_plant``.
# The all-class max is uninformative here because ``not_a_plant`` is a trained
# class: a NAP photo produces a confident NAP logit, so its all-class max lands
# above the plant median and the export auto-disables the floor. Bundles from
# v2.6.13 onward declare ``plant_only``, which separates by construction.
NAP_DETECTOR_CONF_FLOOR = 0.40
NAP_MAX_LOGIT_FLOOR_DEFAULT = 4.0
NAP_HEAD_MASS_FLOOR_DEFAULT = 0.05
MAX_LOGIT_SCOPE_DEFAULT = "all_classes"
_MAX_LOGIT_SCOPES = ("all_classes", "plant_only")

# Classes that are not diseases — used for disease-score and verdict guards.
_NON_DISEASE = {"healthy", "not_a_plant"}

# Viral-mosaic false-positive guard (see ``_postprocess``). Naturally
# variegated / vividly patterned ornamental foliage (Calathea, Maranta,
# Caladium, Coleus, Croton, Aglaonema, Stromanthe…) reads as viral *mosaic*
# to the classifier — the single biggest source of viral false positives.
# We can't fix that without retraining on such cultivars as negatives, so we
# hedge: a caveat warning at any confidence, plus a low-confidence demotion
# when a viral verdict lands below this stricter floor (mid-confidence viral
# calls are mostly variegation, like the 53 % Calathea screenshot).
VIRAL_MIN_CONFIDENCE = 0.65

TOP_K_ALTERNATIVES = 3

# ── Whole-image-vs-crop arbitration ──────────────────────────────────
# Stage 1 crops the leaf before Stage 2 classifies it. On a leaf-centric
# input (a phone shot already framed on one leaf) that extra crop can drop
# context, while on a wide shot the crop can suppress room/background clutter.
# But on a wide indoor shot the crop is exactly what rescues the leaf
# from a room full of background, so we can't simply stop cropping.
#
# Arbitration: classify the WHOLE FRAME too and FOLD IT INTO THE MEAN
# VOTE as one extra member (equal weight to a crop). On a leaf-centric
# input the full-frame verdict reinforces the over-cropped crop and pulls
# the aggregate toward the higher whole-image score; on a wide indoor
# shot the single (mostly-background) full-frame vote is outweighed by
# the leaf crops, so that case does not regress. Averaging probabilities
# keeps temperature / conformal / OOD operating on the same quantity they
# were calibrated on.
#
# Config-toggleable (``limits.fold_whole_frame_vote``) so the notebook can
# ablate the recovery and so an operator can drop it as a latency lever
# without editing source — turning it off removes one full ViT forward per
# scan. Read once at import like every other constant here, so a change
# needs a restart.
FOLD_WHOLE_FRAME_VOTE = get_bool("FOLD_WHOLE_FRAME_VOTE", True)

# ── Blur gate ─────────────────────────────────────────────────────────
# Variance-of-Laplacian score below this number means the photo is too
# unfocused to trust the classifier head on. Plant-coloured blur passes
# the YOLO recall floor and the OOD AND-gate (the detector and the
# classifier-side signal both have to fire, and the not_a_plant head must
# not veto), so without a hard blur check the pipeline cheerfully
# returns "healthy" for a soft-focus brown smear. The 50.0 floor was
# a conservative compatibility threshold, not a validated v9 operating point.
# It is intentionally permissive; v9 must lock and report a served-path quality
# slice before changing or claiming performance for it.
#
# ONE STATISTIC, ONE FLOOR, BOTH TIERS. The on-device Aqua tier runs the same
# quality gate before its classifier (android ``util/BlurGate.kt``): the same
# ITU-R 601-2 integer luma PIL uses for ``convert("L")``, the same 320 px
# long-edge downscale, the same interior 3x3 Laplacian and the same 50.0
# floor. Until 2.7.5 the app scored a quarter-resolution decode (a 12 MP
# frame at ~1000 px) against a floor of 100, so the two tiers rejected
# different photographs. ``tests/test_blur_parity.py`` and the app's
# ``BlurGateTest`` pin the two implementations to one synthetic fixture.
BLUR_LAPLACIAN_FLOOR = 50.0
# The score is computed on a downscaled grayscale copy so we don't pay
# the cost of a full-resolution convolution per request. 320 px is
# small enough that the Laplacian costs <2 ms on CPU and large enough
# that real focus differences stay separable from sensor noise.
BLUR_DOWNSCALE = 320

# Temperature is fitted with the same clamp in the training notebook.  Values
# outside this interval cannot have been produced by that calibration step and
# are treated as a broken sidecar rather than silently changing serving maths.
TEMPERATURE_MIN = 0.05
TEMPERATURE_MAX = 10.0


def _load_temperature(
    classifier_path: Path | None,
    *,
    expected_tta: str | None = None,
) -> float:
    """Look for ``temperature.json`` next to the classifier weights.

    The training notebook writes it automatically. Resolution is strictly
    bundle-relative: a per-family bundle (e.g. ``models/frontier/``) must
    never silently inherit another family's calibration. A missing file means
    calibration is intentionally unavailable and returns 1.0 (no-op). A
    *present* but malformed sidecar is a bundle-integrity error and fails
    pipeline readiness; silently falling back to T=1 would make the API claim
    calibrated probabilities while serving different maths.
    """
    if classifier_path is None:
        return 1.0
    p = Path(classifier_path).parent / "temperature.json"
    if not p.exists():
        return 1.0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid temperature sidecar {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid temperature sidecar {p}: root must be an object")
    raw = data.get("temperature")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RuntimeError(
            f"Invalid temperature sidecar {p}: temperature must be numeric"
        )
    temperature = float(raw)
    if (
        not math.isfinite(temperature)
        or not TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX
    ):
        raise RuntimeError(
            f"Invalid temperature sidecar {p}: temperature must be finite and in "
            f"[{TEMPERATURE_MIN}, {TEMPERATURE_MAX}], got {raw!r}"
        )

    declared_tta = data.get("tta")
    if declared_tta is not None and not isinstance(declared_tta, str):
        raise RuntimeError(f"Invalid temperature sidecar {p}: tta must be a string")
    if expected_tta is not None and declared_tta is not None:
        if declared_tta.strip().lower() != expected_tta.strip().lower():
            raise RuntimeError(
                f"Temperature sidecar {p} was fit with tta={declared_tta!r}, "
                f"but serving uses tta={expected_tta!r}"
            )
    if data.get("serving_contract") is None:
        logger.warning(
            "Temperature sidecar %s has no model/preprocess serving-contract "
            "binding; scaling is loaded but its calibration claim is advisory",
            p,
        )
    logger.info("Loaded temperature T=%.3f from %s", temperature, p)
    return temperature


def _served_ood_gate_contract(meta: dict) -> tuple[bool, str]:
    """Validate that an OOD threshold was calibrated as it is served.

    Historical thresholds were fitted one image at a time, while serving folds
    crop and whole-frame members and then takes the minimum embedding distance
    / maximum MaxLogit. Applying the old threshold to that different statistic
    has no measured operating point. The optional secondary OOD overlay is
    therefore enabled only by an explicit, exact sidecar contract.
    """
    ood = meta.get("ood") if isinstance(meta, dict) else None
    contract = ood.get("served_gate") if isinstance(ood, dict) else None
    if not isinstance(contract, dict):
        return False, "missing ood.served_gate object"

    expected = {
        "schema_version": 1,
        "calibration_mode": "full_served_pipeline",
        "embedding_aggregation": "min_member_distance",
        "max_logit_aggregation": "max_member_logit",
        "detector_confidence_floor": NAP_DETECTOR_CONF_FLOOR,
        "fold_whole_frame_vote": FOLD_WHOLE_FRAME_VOTE,
    }
    for key, wanted in expected.items():
        got = contract.get(key)
        # bool is an int in Python; keep JSON types strict so true cannot stand
        # in for schema_version=1 or detector_confidence_floor=1.
        if isinstance(wanted, bool):
            matches = isinstance(got, bool) and got is wanted
        elif isinstance(wanted, (int, float)):
            matches = (
                not isinstance(got, bool)
                and isinstance(got, (int, float))
                and math.isfinite(float(got))
                and float(got) == float(wanted)
            )
        else:
            matches = isinstance(got, str) and got == wanted
        if not matches:
            return False, f"ood.served_gate.{key}={got!r}, expected {wanted!r}"
    return True, "matching full-served-pipeline calibration contract"


def _resolve_served_gate_thresholds(meta: dict) -> tuple[float | None, float | None]:
    """Thresholds fitted on the *served* statistic (``ood.served_gate``).

    Returns ``(embedding_threshold, max_logit_floor)``. Each is ``None`` when
    the sidecar declares no served value for it (the notebook writes ``null``
    when a signal did not separate on the served statistic). A declared value
    must be a finite number in range; anything else fails readiness, because
    silently falling back to the single-image threshold in ``ood.embedding``
    is exactly the mis-calibration the served-gate contract exists to prevent.
    """
    ood = meta.get("ood") if isinstance(meta, dict) else None
    gate = ood.get("served_gate") if isinstance(ood, dict) else None
    if not isinstance(gate, dict):
        return None, None
    resolved: list[float | None] = []
    for key, upper in (("embedding_threshold", 2.0), ("max_logit_floor", None)):
        val = gate.get(key)
        if val is None:
            resolved.append(None)
            continue
        if (
            isinstance(val, bool)
            or not isinstance(val, (int, float))
            or not math.isfinite(float(val))
            or float(val) < 0.0
            or (upper is not None and float(val) > upper)
        ):
            raise RuntimeError(
                f"classes.json ood.served_gate.{key} must be null or a finite number "
                f"in range, got {val!r}"
            )
        resolved.append(float(val))
    return resolved[0], resolved[1]


def _require_contract_value(contract: dict, dotted: str, expected) -> None:
    """Require one serving-manifest value with strict, finite type semantics."""
    current: object = contract
    for key in dotted.split("."):
        if not isinstance(current, dict) or key not in current:
            raise BundleLoadError(f"serving_contract is missing {dotted}")
        current = current[key]
    if isinstance(expected, bool):
        matches = isinstance(current, bool) and current is expected
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        matches = (
            not isinstance(current, bool)
            and isinstance(current, (int, float))
            and math.isfinite(float(current))
            and math.isclose(float(current), float(expected), rel_tol=0.0, abs_tol=1e-6)
        )
    elif isinstance(expected, list):
        matches = isinstance(current, list) and len(current) == len(expected)
        if matches:
            for got, wanted in zip(current, expected, strict=True):
                if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
                    if (
                        isinstance(got, bool)
                        or not isinstance(got, (int, float))
                        or not math.isfinite(float(got))
                        or not math.isclose(
                            float(got), float(wanted), rel_tol=0.0, abs_tol=1e-6,
                        )
                    ):
                        matches = False
                        break
                elif got != wanted:
                    matches = False
                    break
    else:
        matches = type(current) is type(expected) and current == expected
    if not matches:
        raise BundleLoadError(
            f"serving_contract.{dotted}={current!r}, runtime requires {expected!r}"
        )


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


def _resolve_head_mass_floor(meta: dict) -> float:
    """Read the NAP head-veto floor from classes.json ``ood`` section.

    Same contract as :func:`_resolve_max_logit_floor`, with one deliberate
    difference: the non-strict legacy default is **on**. Strict production
    manifests require the key explicitly. Set
    ``ood.nap_head_mass_floor: null`` to restore the historical two-of-two gate.
    """
    if not isinstance(meta, dict):
        return NAP_HEAD_MASS_FLOOR_DEFAULT
    ood = meta.get("ood")
    if not isinstance(ood, dict) or "nap_head_mass_floor" not in ood:
        return NAP_HEAD_MASS_FLOOR_DEFAULT
    val = ood.get("nap_head_mass_floor")
    if val is None:
        return 0.0
    try:
        floor = float(val)
    except (TypeError, ValueError):
        return NAP_HEAD_MASS_FLOOR_DEFAULT
    return min(max(0.0, floor), 1.0)


def _resolve_max_logit_scope(meta: dict) -> str:
    """Read ``ood.max_logit_scope`` from classes.json.

    Defaults to ``"all_classes"`` so every bundle exported before the scope key
    existed keeps its historical numerics exactly. An unrecognised value also
    falls back to the default rather than failing the boot — the floor is a
    *fallback* signal (the embedding bank is primary), so an unknown scope must
    not take the server down.
    """
    if not isinstance(meta, dict):
        return MAX_LOGIT_SCOPE_DEFAULT
    ood = meta.get("ood")
    if not isinstance(ood, dict):
        return MAX_LOGIT_SCOPE_DEFAULT
    scope = ood.get("max_logit_scope")
    if scope in _MAX_LOGIT_SCOPES:
        return str(scope)
    if scope is not None:
        logger.warning("Unknown OOD max-logit scope; using safe default")
    return MAX_LOGIT_SCOPE_DEFAULT


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

    # Cap the number of bboxes we re-classify per request. NOTE: the
    # backbone is now a frozen DINOv2 ViT-S/14, which is markedly heavier
    # per crop on CPU than the retired MobileNetV3-L this cap was first
    # tuned for — every crop here is a full ViT forward (they are batched
    # into one sess.run, but the FLOPs add up). Crops + the folded
    # whole-frame vote are the dominant cost of a scan, so lower this (or
    # FOLD_WHOLE_FRAME_VOTE) before anything else if latency must drop
    # further. Inference now uses all CPU cores (ONNX_INTRA_OP_THREADS=0).
    _MAX_RECLASSIFY = 4

    def __init__(
        self,
        detector_path: Path,
        classifier_path: Path,
        *,
        require_bundle_manifest: bool = False,
    ):
        self.verified_bundle: VerifiedBundle | None = None
        if require_bundle_manifest:
            self.verified_bundle = verify_bundle(classifier_path.parent)
            classifier_path = self.verified_bundle.classifier_path
            detector_path = self.verified_bundle.detector_path
        self.detector = LeafDetector(detector_path)
        self.classifier = DiseaseClassifier(
            classifier_path,
            exact_model_path=self.verified_bundle is not None,
        )
        self.temperature = _load_temperature(
            classifier_path,
            expected_tta=getattr(self.classifier, "tta", None),
        )
        meta = getattr(self.classifier, "meta", None) or {}
        (
            self._secondary_ood_gate_enabled,
            self._secondary_ood_gate_contract_reason,
        ) = _served_ood_gate_contract(meta)
        self.nap_max_logit_floor = _resolve_max_logit_floor(meta)
        self.nap_max_logit_scope = _resolve_max_logit_scope(meta)
        self.nap_head_mass_floor = _resolve_head_mass_floor(meta)
        # The ONLY thresholds the overlay applies are the served-statistic ones
        # from ``ood.served_gate`` (fitted in notebook §6 through detector ->
        # crops -> whole-frame fold -> min distance / max logit). The
        # single-image ``ood.embedding.threshold`` and ``ood.max_logit_floor``
        # stay in the sidecar as calibration provenance and are never served.
        self._served_embedding_threshold: float | None = None
        if self._secondary_ood_gate_enabled:
            served_emb, served_ml = _resolve_served_gate_thresholds(meta)
            self._served_embedding_threshold = served_emb
            self.nap_max_logit_floor = served_ml if served_ml is not None else 0.0
        # Which per-crop key carries the score this bundle's floor was fit on.
        self._max_logit_key = (
            "plant_max_logit"
            if self.nap_max_logit_scope == "plant_only"
            else "max_logit"
        )
        self.conformal: ConformalConfig | None = load_conformal_config(
            classifier_path.parent
        )
        # Embedding-space OOD (k-NN on the DINOv2 CLS vector). Replaces the
        # MaxLogit term in the NAP AND-gate when the bundle ships a bank;
        # ``None`` keeps the historical MaxLogit-floor behaviour.
        self.embedding_ood: EmbeddingOODScorer | None = load_embedding_ood(
            classifier_path.parent, meta
        )
        if self.verified_bundle is not None:
            self._validate_bundle_serving_contract(meta)
        if self._served_embedding_threshold is not None and self.embedding_ood is not None:
            logger.info(
                "OOD: embedding k-NN gate active on the served statistic "
                "(min member distance > %.4f; single-image sidecar threshold %.4f "
                "is provenance only) — supersedes the MaxLogit floor.",
                self._served_embedding_threshold,
                self.embedding_ood.threshold,
            )
        if not self._secondary_ood_gate_enabled:
            logger.warning(
                "Secondary OOD overlay is advisory/OFF: %s. Natural trained "
                "not_a_plant predictions remain active; detector confidence "
                "alone can never reject an image.",
                self._secondary_ood_gate_contract_reason,
            )
        elif (
            (self.embedding_ood is None or self._served_embedding_threshold is None)
            and self.nap_max_logit_floor <= 0
        ):
            logger.warning(
                "Secondary OOD overlay has a matching serving contract but no "
                "usable classifier-side signal; it is OFF rather than "
                "degrading to detector-only rejection.",
            )
        logger.info(
            "Pipeline ready  classifier=%s backend  T=%.3f  "
            "MaxLogit floor=%s (%s)  NAP head veto=%s  conformal=%s",
            self.classifier.backend,
            self.temperature,
            "off" if self.nap_max_logit_floor <= 0 else f"{self.nap_max_logit_floor:.2f}",
            self.nap_max_logit_scope,
            "off" if self.nap_head_mass_floor <= 0
            else f"P(nap)<{self.nap_head_mass_floor:.2f}",
            "off" if self.conformal is None
            else f"{self.conformal.method}@{self.conformal.coverage_target:.0%}",
        )

    def _validate_bundle_serving_contract(self, meta: dict) -> None:
        """Bind manifest claims to every live preprocessing/routing lever."""
        assert self.verified_bundle is not None
        contract = self.verified_bundle.manifest["serving_contract"]
        selected = self.verified_bundle.classifier_path.name
        expected = {
            "schema_version": 1,
            "detector.path": "detector.onnx",
            "detector.class_names": [self.detector.class_name],
            "detector.img_size": self.detector.img_size,
            "detector.min_confidence": self.detector.min_confidence,
            "detector.nms_iou": self.detector.nms_iou,
            "detector.max_detections": self.detector.max_detections,
            "crop.max_crops": self._MAX_RECLASSIFY,
            "crop.min_size_px": MIN_CROP_SIZE,
            "crop.min_area_fraction": MIN_BBOX_AREA_FRAC,
            "crop.padding_ratio": CROP_PADDING_RATIO,
            "crop.fold_whole_frame_vote": FOLD_WHOLE_FRAME_VOTE,
            "crop.member_selection": "detector_confidence_descending",
            "crop.vote_aggregation": "equal_mean_probabilities",
            "crop.whole_frame_vote_weight": 1.0,
            "classifier.path": selected,
            "classifier.img_size": self.classifier.img_size,
            "classifier.eval_resize_ratio": self.classifier.eval_resize_ratio,
            "classifier.resize": "shorter_side",
            "classifier.crop": "center",
            "classifier.normalize_mean": self.classifier.norm_mean.tolist(),
            "classifier.normalize_std": self.classifier.norm_std.tolist(),
            "classifier.tta": self.classifier.tta,
            "classifier.tta_aggregation": "mean_logits",
            "classifier.output_semantics": "logits",
            "blur.enabled": True,
            "blur.policy": "abstain",
            "blur.variance_laplacian_floor": BLUR_LAPLACIAN_FLOOR,
            "blur.downscale_max_side": BLUR_DOWNSCALE,
            "ood.policy": "detector_and_classifier_signal_with_nap_head_veto",
            "ood.detector_confidence_floor": NAP_DETECTOR_CONF_FLOOR,
            "ood.classifier_signal_priority": "embedding_knn_else_max_logit",
            "ood.embedding_aggregation": "min_member_distance",
            "ood.max_logit_aggregation": "max_member_logit",
            "ood.nap_head_veto": "veto_when_probability_below_configured_floor",
            "ood.served_gate_enabled": self._secondary_ood_gate_enabled,
            "ood.config_source": "classes.json#ood",
        }
        for dotted, wanted in expected.items():
            _require_contract_value(contract, dotted, wanted)

        ood = meta.get("ood") if isinstance(meta, dict) else None
        declared_gate = ood.get("served_gate") if isinstance(ood, dict) else None
        _require_contract_value(contract, "ood.served_gate", declared_gate)
        logger.info(
            "Verified immutable serving bundle manifest=%s contract=%s",
            self.verified_bundle.manifest_sha256[:16],
            self.verified_bundle.serving_contract_sha256[:16],
        )

    # ── main entry point ─────────────────────────────────────────────

    def analyze(self, img: Image.Image) -> dict:
        t0 = time.perf_counter()

        # Blur gate. The classifier was trained on in-focus phone shots
        # and silently hallucinates a plant verdict on soft-focus brown
        # smears — neither the YOLO recall floor nor the three-of-three
        # OOD gate catches that case. Short-circuit
        # early so the user gets actionable "retake the photo" feedback
        # instead of a confident wrong diagnosis.
        focus_score = _variance_of_laplacian(img)
        if focus_score < BLUR_LAPLACIAN_FLOOR:
            logger.info(
                "Blur gate triggered: var-of-Laplacian=%.1f < %.1f → quality abstention",
                focus_score, BLUR_LAPLACIAN_FLOOR,
            )
            return self._too_blurry_response(img, t0)

        stage1 = self.detector.detect(img)
        detections = stage1["detections"]

        logger.info("Detector stage completed")

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
        # detector confidence combined with a firing classifier-side OOD
        # signal (embedding k-NN distance, or the MaxLogit floor when no
        # bank ships) is the classic OOD signature: the detector glommed
        # onto a plant-coloured texture and the classifier had no clean
        # answer to give. Mark the response as an abstention — unless the
        # not_a_plant head vetoes — without rewriting measured probabilities.
        det_conf = float(stage1.get("confidence", 0.0))
        if self._should_route_to_nap(classification, det_conf):
            classification = self._mark_ood_rejected(classification)

        logger.info("Classifier stage completed")

        final = _apply_postprocessing_rules(classification, self.classifier.CLASS_NAMES)
        # Compute the operational disease verdict from the final, canonical
        # probability-backed result.  This also honours an OOD abstention;
        # doing it before postprocessing allowed response fields to disagree.
        is_diseased = self._is_diseased(final)

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
        t0: float,
    ) -> dict:
        """Build an explicit image-quality abstention for unfocused input.

        The classifier is intentionally not run, so there is no probability
        distribution to report. Older code invented P(not_a_plant)=1 here;
        that looked calibrated despite having no model evidence. ``unknown``
        plus empty diagnostics and the explicit abstention fields truthfully
        distinguish a quality rejection from a natural trained-head NAP.
        """
        final = {
            "class_name": "unknown",
            "confidence": 0.0,
            "all_probs": {},
            "top_k": [],
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
        return self._build_response(
            final=final,
            severity=severity,
            is_diseased=False,
            stage1_confidence=0.0,
            regions=[],
            primary_region=None,
            image_size=img.size,
            t0=t0,
        )

    def _analyze_no_detection(self, img: Image.Image, t0: float) -> dict:
        cls_full = self.classifier.classify(img)
        calibrated = _apply_temperature(cls_full["all_probs"], self.temperature)
        # Score the whole-frame embedding for OOD too, so a non-leaf image that
        # Stage 1 missed entirely (no bbox) still gets the classifier-side OOD
        # check — not just the not_a_plant head. The k-NN bank includes
        # whole-frame plant images, so a genuine wide plant shot scores
        # in-distribution and keeps its verdict.
        embedding = cls_full.get("embedding")
        ood_score = self._embedding_ood_score(
            [embedding] if embedding is not None else []
        )
        classification = {
            **cls_full,
            "all_probs": calibrated,
            "ood_score": ood_score,
            "_voted_over": 0,
        }
        # Secondary NAP gate on the no-detection path. det_conf is 0 here (Stage
        # 1 found nothing leaf-like), so we require a real classifier-side OOD
        # signal — without it the gate would route every no-detection input to
        # NAP, including wide real-plant shots YOLO simply failed to box.
        if self._should_route_to_nap(classification, det_conf=0.0):
            classification = self._mark_ood_rejected(classification)
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
        abstained, abstention_reason = _derive_abstention(final)
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "abstained": abstained,
            "abstention_reason": abstention_reason,
            "verdict_status": "abstained" if abstained else "accepted",
            "severity": severity,
            "prediction_set": self._build_prediction_set(
                final["all_probs"],
                final["class_name"],
                rejected=abstained,
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
            not classification.get("_ood_gated", False)
            and classification["class_name"] not in _NON_DISEASE
            and classification["confidence"] >= DISEASE_MIN_CONFIDENCE
        )

    def _should_route_to_nap(
        self,
        classification: dict,
        det_conf: float,
    ) -> bool:
        """Three-of-three OOD gate. We abstain when the
        detector AND the classifier's OOD score both signal "this is not a
        leaf" AND the trained ``not_a_plant`` head does not veto:

            det_conf < NAP_DETECTOR_CONF_FLOOR
            AND classifier-side OOD signal fires
            AND P(not_a_plant) >= NAP_HEAD_MASS_FLOOR

        The first two must independently fire. Either alone is too noisy: a
        tight crop of an unfamiliar plant can look OOD to the classifier
        even when the detector is confident, and vice versa.

        The third is a **veto**, added once it was measured that neither of
        the first two consults the one head trained to answer this exact
        question — and that the head is by far the better separator on the
        populations we have (see ``NAP_HEAD_MASS_FLOOR``). A near-zero NAP
        mass means the classifier is certain this is a plant; an
        embedding-distance score that fires anyway is measuring
        *unfamiliar*, which is not the same claim.

        The classifier-side signal is, in priority order:
          1. **Embedding k-NN distance** (``ood.py``) when the bundle ships
             a bank — ``ood_score > threshold``. This is the live
             replacement for MaxLogit, which went dead once the NAP head
             grew over-confident (plant/NAP MaxLogit separation ≤ 0).
          2. **MaxLogit floor** (``max_logit < ml_floor``) when no bank is
             shipped but ``ood.max_logit_floor`` is a positive number.
          3. Otherwise no classifier signal — the secondary overlay stays off.

        The entire overlay also requires ``ood.served_gate`` to state that its
        thresholds were calibrated with the same crop/whole aggregation and
        detector runtime, and it applies ONLY the thresholds declared there
        (``embedding_threshold`` / ``max_logit_floor``). The one-image-at-a-time
        thresholds in ``ood.embedding`` / ``ood.max_logit_floor`` are
        calibration provenance and cannot reject.
        """
        if not getattr(self, "_secondary_ood_gate_enabled", False):
            return False
        # Served-statistic threshold only. ``None`` (no bank, or the notebook
        # found the embedding signal did not separate on the served statistic)
        # hands the decision to the served MaxLogit floor, then to no signal.
        served_threshold = getattr(self, "_served_embedding_threshold", None)
        ood_threshold = (
            served_threshold
            if self.embedding_ood is not None and served_threshold is not None
            else None
        )
        probs = classification.get("all_probs") or {}
        triggered, desc = _decide_nap_route(
            class_name=classification["class_name"],
            class_names=self.classifier.CLASS_NAMES,
            det_conf=det_conf,
            ood_score=classification.get("ood_score"),
            ood_threshold=ood_threshold,
            ml_floor=self.nap_max_logit_floor,
            max_logit=self._scoped_max_logit(classification),
            ml_scope=self.nap_max_logit_scope,
            head_nap=probs.get("not_a_plant"),
            head_mass_floor=self.nap_head_mass_floor,
        )
        if triggered:
            logger.info("OOD gate triggered (%s) → abstain; keep classifier probabilities", desc)
        return triggered

    def _build_prediction_set(
        self,
        probs: dict[str, float],
        class_name: str | None = None,
        *,
        rejected: bool = False,
    ) -> dict | None:
        """Return the conformal prediction set or ``None`` if unconfigured.

        Honest UX: clients should render the *set* as advisory alternatives
        rather than pretending the top-1 was a single confident answer. The
        ``coverage`` field is ``ConformalConfig.coverage_target``: the
        notebook's product target when the sidecar carries the out-of-sample
        audit that backs it, else the nominal ``1 - alpha`` (see
        ``conformal.py``). Neither is delivered coverage measured on a locked
        independent set. The dict is keyed by ``classes`` / ``coverage`` /
        ``method`` so the client doesn't need to know about the calibration
        internals.

        The conformal calibration covers accepted *plant* inputs only.
        Attaching its coverage statement to a natural ``not_a_plant`` result
        or an OOD-gated abstention would over-claim, so suppress it for both.
        """
        cfg = self.conformal
        if cfg is None or not probs:
            return None
        if class_name == "not_a_plant" or rejected:
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

        # Whole-image-vs-crop arbitration (see FOLD_WHOLE_FRAME_VOTE): the
        # full frame is classified alongside the crops and folded into the
        # mean vote as one extra member. Batch it with the crops so it
        # costs a single extra slot in the same forward, not a separate
        # sess.run round-trip.
        fold_whole = FOLD_WHOLE_FRAME_VOTE
        batch = crops + ([img] if fold_whole else [])
        cls_results = self.classifier.classify_batch(batch)
        crop_results = cls_results[: len(crops)]
        whole_result = cls_results[len(crops)] if fold_whole else None

        results: list[tuple[dict, dict]] = []
        member_probs: list[dict[str, float]] = []
        embeddings: list = []
        max_logit_seen = -float("inf")
        plant_max_logit_seen = -float("inf")

        for box, cls_res in zip(ranked, crop_results, strict=True):
            calibrated = _apply_temperature(cls_res["all_probs"], self.temperature)
            results.append((box, {**cls_res, "all_probs": calibrated}))
            member_probs.append(calibrated)
            if cls_res.get("embedding") is not None:
                embeddings.append(cls_res["embedding"])
            ml = float(cls_res.get("max_logit", 0.0))
            if ml > max_logit_seen:
                max_logit_seen = ml
            pml = self._scoped_max_logit(cls_res, key="plant_max_logit")
            if pml > plant_max_logit_seen:
                plant_max_logit_seen = pml

        # The whole frame votes on the verdict but never becomes the
        # highlighted ``primary_box`` (it has no bbox); its OOD signal
        # still counts toward the strongest in-frame MaxLogit.
        if whole_result is not None:
            whole_cal = _apply_temperature(whole_result["all_probs"], self.temperature)
            member_probs.append(whole_cal)
            if whole_result.get("embedding") is not None:
                embeddings.append(whole_result["embedding"])
            ml = float(whole_result.get("max_logit", 0.0))
            if ml > max_logit_seen:
                max_logit_seen = ml
            pml = self._scoped_max_logit(whole_result, key="plant_max_logit")
            if pml > plant_max_logit_seen:
                plant_max_logit_seen = pml

        n_boxes = max(len(member_probs), 1)
        mean_probs = _fold_votes(member_probs, self.classifier.CLASS_NAMES)
        # Embedding-space OOD: take the MIN k-NN distance across the voted
        # members (crops + whole frame) — the strongest in-distribution
        # evidence we have. If even the closest member is far from the
        # plant manifold, the crop is OOD. ``None`` when no bank is shipped.
        ood_score = self._embedding_ood_score(embeddings)

        top1_class = max(mean_probs, key=mean_probs.get)
        top1_conf = float(mean_probs[top1_class])

        # Invariant: do NOT reintroduce a "weak disease → healthy" fallback.
        # It manufactured fake-healthy distributions out of real disease
        # predictions — see the module docstring and the
        # ``DISEASE_MIN_CONFIDENCE`` comment for why it was removed, and
        # tests/test_pipeline_helpers.py for the guard.

        primary_box = self._select_primary_box(
            results=results,
            top1_class=top1_class,
        )

        aggregated_cls = {
            "class_name": top1_class,
            "confidence": top1_conf,
            "all_probs": mean_probs,
            "max_logit": max_logit_seen if max_logit_seen != -float("inf") else 0.0,
            "plant_max_logit": (
                plant_max_logit_seen
                if plant_max_logit_seen != -float("inf")
                else 0.0
            ),
            "ood_score": ood_score,
            "low_confidence": top1_conf < HEALTHY_MIN_CONFIDENCE,
            "_voted_over": n_boxes,
        }
        return primary_box, aggregated_cls

    def _scoped_max_logit(self, classification: dict, key: str | None = None) -> float:
        """MaxLogit under this bundle's declared scope.

        ``key=None`` uses ``self._max_logit_key`` (the scope the floor was fit
        on). A legacy classifier result that predates ``plant_max_logit``
        silently falls back to the all-class ``max_logit``, so an old bundle +
        new server behaves exactly as before.
        """
        k = key or self._max_logit_key
        val = classification.get(k)
        if val is None:
            val = classification.get("max_logit", 0.0)
        return float(val)

    def _embedding_ood_score(self, embeddings: list) -> float | None:
        """Min k-NN OOD distance over the member embeddings, or ``None``.

        ``None`` when no embedding bank is shipped or no member carried an
        embedding (legacy single-output graph). NaN per-member scores
        (dim mismatch) are skipped; if all are NaN the result is ``None``.
        """
        if self.embedding_ood is None or not embeddings:
            return None
        scores = [self.embedding_ood.score(e) for e in embeddings]
        finite = [s for s in scores if s == s]  # drop NaN
        return min(finite) if finite else None

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
        # The ranking also does the right thing when the aggregate verdict is
        # healthy: the most-diseased region still wins,
        # so the result screen highlights the suspicious leaf, not a
        # picture-perfect one beside it.
        return ranked[0][0]

    @staticmethod
    def _mark_ood_rejected(classification: dict) -> dict:
        """Mark an OOD abstention without manufacturing a class probability.

        ``not_a_plant`` is a trained softmax class.  Detector confidence and
        embedding distance are not probabilities for that class, so combining
        them into a made-up NAP confidence would corrupt calibration and make
        ``class_name`` disagree with ``all_probs``.  Explicit abstention fields
        plus the legacy warning carry the rejection; the raw calibrated
        distribution stays available as diagnostic evidence and for ``top_k``.
        """
        return {**classification, "_ood_gated": True}

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


def _decide_nap_route(
    *,
    class_name: str,
    class_names: list[str],
    det_conf: float,
    ood_score: float | None,
    ood_threshold: float | None,
    ml_floor: float,
    max_logit: float,
    ml_scope: str = MAX_LOGIT_SCOPE_DEFAULT,
    head_nap: float | None = None,
    head_mass_floor: float = 0.0,
) -> tuple[bool, str]:
    """Pure NAP-gate decision (exposed for tests).

    Returns ``(route_to_nap, reason)``. The gate is three-of-three: the
    detector must be unsure (``det_conf < NAP_DETECTOR_CONF_FLOOR``) AND
    the classifier-side OOD signal must fire AND the ``not_a_plant`` head must
    not veto. The classifier signal is, in
    priority order: the embedding k-NN distance (``ood_score > ood_threshold``)
    when a bank is shipped and an embedding was produced, else the MaxLogit
    floor (``max_logit < ml_floor``) when that floor is positive, else no
    signal at all — in which case the secondary gate stays off.

    The head veto applies only when ``head_mass_floor`` is positive and a
    ``head_nap`` probability was supplied; a caller that has neither gets the
    historical two-of-two behaviour unchanged.
    """
    # A natural not_a_plant top-1 is already an honest trained-head rejection.
    # Healthy, however, is still a plant-content claim and must be eligible for
    # the same OOD checks as a disease result; bypassing it let sharp junk that
    # happened to map to healthy evade the rejection gate entirely.
    if class_name == "not_a_plant" or "not_a_plant" not in class_names:
        return False, "already-nap-or-no-nap-class"

    # Head veto first — it is the cheapest and the most decisive of the three.
    if head_mass_floor > 0 and head_nap is not None and head_nap < head_mass_floor:
        return False, (
            f"head P(not_a_plant)={head_nap:.4f}<{head_mass_floor:.2f} "
            f"→ classifier is confident this IS a plant, veto"
        )

    detector_low = det_conf < NAP_DETECTOR_CONF_FLOOR

    if ood_threshold is not None and ood_score is not None:
        classifier_ood = ood_score > ood_threshold
        has_signal = True
        desc = f"ood_score={ood_score:.3f}>{ood_threshold:.3f}={classifier_ood}"
    elif ml_floor > 0:
        classifier_ood = max_logit < ml_floor
        has_signal = True
        desc = (
            f"max_logit[{ml_scope}]={max_logit:.3f}<{ml_floor:.2f}"
            f"={classifier_ood}"
        )
    else:
        classifier_ood = False
        has_signal = False
        desc = "no-classifier-signal"

    if not has_signal:
        # Detector confidence is localisation evidence, not a calibrated OOD
        # classifier. It may participate in the AND gate but can never be the
        # sole reason to reject an image.
        return False, (
            f"det={det_conf:.3f}<{NAP_DETECTOR_CONF_FLOOR:.2f}={detector_low}, "
            f"{desc} (classifier signal absent → keep verdict)"
        )

    triggered = detector_low and classifier_ood
    return triggered, (
        f"det={det_conf:.3f}<{NAP_DETECTOR_CONF_FLOOR:.2f}={detector_low}, {desc}"
    )


ABSTENTION_REASON_OOD = "ood"
ABSTENTION_REASON_QUALITY = "image_quality"
ABSTENTION_REASON_NAP = "not_a_plant"
ABSTENTION_REASON_UNKNOWN = "unknown"


def _derive_abstention(result: dict) -> tuple[bool, str | None]:
    """Return the public abstention verdict for a pipeline or cached result.

    Warning-based derivation is part of the contract so an API adapter can
    safely upgrade cached responses written before explicit fields existed.
    OOD and image quality take precedence over a synthetic blur response's
    ``not_a_plant`` class; a natural trained-head NAP argmax is reported
    separately as ``not_a_plant``. Explicit ``abstention_reason`` /
    ``abstained`` / ``verdict_status`` fields cover adapters that already
    normalised the contract.
    """
    warnings = {
        str(item).strip().lower()
        for item in (result.get("warnings") or [])
        if isinstance(item, str)
    }
    if result.get("_ood_gated") or "nap_ood_gated" in warnings:
        return True, ABSTENTION_REASON_OOD
    if "blurry_image" in warnings:
        return True, ABSTENTION_REASON_QUALITY
    explicit_reason = result.get("abstention_reason")
    if isinstance(explicit_reason, str) and explicit_reason.strip():
        return True, explicit_reason.strip().lower()
    if result.get("class_name") == "not_a_plant":
        return True, ABSTENTION_REASON_NAP
    if result.get("abstained") is True or result.get("verdict_status") == "abstained":
        return True, ABSTENTION_REASON_UNKNOWN
    return False, None


def _fold_votes(
    member_probs: list[dict[str, float]],
    class_names: list[str],
) -> dict[str, float]:
    """Mean-average a list of per-member probability dicts into one vote.

    Each member is an already-temperature-calibrated ``all_probs`` dict
    (one per leaf crop, plus the whole frame when
    ``FOLD_WHOLE_FRAME_VOTE`` is on). Averaging is over the union of
    ``class_names`` so a member that somehow omitted a class contributes
    0 mass for it rather than dropping the class from the vote. Exposed
    for tests: it carries the whole-image-vs-crop arbitration math, so a
    confident whole-frame vote folded into a weak crop vote must pull the
    aggregate toward the whole-frame verdict.
    """
    sum_probs: dict[str, float] = {n: 0.0 for n in class_names}
    for probs in member_probs:
        for n, p in probs.items():
            sum_probs[n] = sum_probs.get(n, 0.0) + float(p)
    n = max(len(member_probs), 1)
    return {name: total / n for name, total in sum_probs.items()}


def compute_severity(
    *,
    class_name: str,
    confidence: float,
    healthy_prob: float,
    primary_box: dict | None,
    image_size: tuple[int, int],
    is_diseased: bool,
) -> str:
    """Return the conservative severity placeholder supported by this model.

    The detector box localises a *leaf*, not a lesion, and classifier
    confidence estimates label certainty, not affected-area extent.  Neither
    can identify mild/medium/severe disease without severity-labelled data.
    Keep the existing response enum compatible by returning ``low`` rather
    than inventing a clinical-looking level.  The otherwise-unused arguments
    remain in the signature for API compatibility and make the missing target
    explicit to future training work.
    """
    return "low"


def _apply_postprocessing_rules(
    classification: dict,
    class_names: list[str],
) -> dict:
    """Probability-preserving postprocessing.

    Checks, in order:
      * canonicalise ``class_name`` / ``confidence`` to the measured argmax;
        postprocessing may warn but never substitute a lower-probability class;
      * ``uncertain_healthy`` — top-1 is healthy but confidence/margin is below
        the soft thresholds, or a disease clears ``HEALTHY_DISEASE_FLOOR``.
        Surfaces the suspected disease (``disease_suspected:<class>``) +
        alternatives, no class swap.
      * ``low_confidence`` — top-1 below the healthy threshold.
    """
    probs = classification["all_probs"]
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    warnings: list[str] = []

    top1_name, top1_prob = ordered[0]
    top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0
    classification = {
        **classification,
        "class_name": top1_name,
        "confidence": float(top1_prob),
    }

    top_k = [
        {"class": name, "confidence": float(conf)}
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
            logger.info("Healthy-result uncertainty warning triggered")

    if classification.get("_ood_gated"):
        warnings.append("nap_ood_gated")

    # Re-evaluate after temperature scaling / vote aggregation.  The
    # no-detection path starts from a per-image classifier result that does not
    # carry this flag, and a stale pre-calibration flag must not override the
    # probability source of truth.
    if classification.get("low_confidence") or top1_prob < HEALTHY_MIN_CONFIDENCE:
        warnings.append("low_confidence")

    # Viral-mosaic guard — see VIRAL_MIN_CONFIDENCE. Naturally variegated
    # ornamentals are the dominant viral false positive, so always attach a
    # caveat on a viral verdict, and demote mid-confidence viral calls.
    if classification["class_name"] == "viral_disease":
        if "viral_variegation_caveat" not in warnings:
            warnings.append("viral_variegation_caveat")
        if (
            float(classification["confidence"]) < VIRAL_MIN_CONFIDENCE
            and "low_confidence" not in warnings
        ):
            warnings.append("low_confidence")

    # Very-low-confidence floor — purely a UI hint, no class swap.
    # The canonical argmax confidence is what the user sees. Any verdict below
    # the floor — including a disease that the model emitted at
    # 31 % on a junk image — earns the warning so the UI can render
    # a "please retake the photo" banner.
    if float(classification["confidence"]) < VERY_LOW_CONFIDENCE_FLOOR:
        warnings.append("very_low_confidence")

    result = {
        "class_name": classification["class_name"],
        "confidence": float(classification["confidence"]),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
    # Internal marker consumed by ``_is_diseased`` and conformal output;
    # ``_build_response`` turns it into the explicit, versioned abstention
    # contract while preserving these raw probability diagnostics.
    if classification.get("_ood_gated"):
        result["_ood_gated"] = True
    return result
