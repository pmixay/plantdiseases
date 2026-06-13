"""Model-trust auto-labeling for the data-labeling tool.

Runs an ingested candidate through the calibrated gamma classifier and decides,
from the conformal prediction set (primary) + temperature-calibrated confidence
+ the OOD/not-a-plant verdict, whether to:

  * ``auto_yes``  — the model confidently agrees with the proposed class,
  * ``auto_no``   — the model confidently disagrees / it isn't a plant,
  * ``pending``   — the model is uncertain (the confusing tail) → a human.

See ``docs/DATASET_LABELING_PLAN.md``. Fail-soft: any error routes to
``pending`` so nothing is silently lost.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def route_scan(
    class_name: str, confidence: float | None,
    prediction_set_classes: list[str] | None, *,
    trust_conformal: bool = True, conf_high: float = 0.85,
) -> str:
    """Route a recorded production scan from its ALREADY-computed analyze
    result (no second inference). The model's prediction *is* the proposed
    class, so ``auto_no`` is impossible here — the scan is either confidently
    self-consistent (``auto_yes``: singleton conformal set == top-1, or
    high calibrated confidence when conformal is off/absent) or suspicious
    (``pending``: multi-class set / low confidence) and goes to a human."""
    classes = list(prediction_set_classes or [])
    if trust_conformal and classes:
        if classes == [class_name]:
            return "auto_yes"
        return "pending"
    if float(confidence or 0.0) >= conf_high:
        return "auto_yes"
    return "pending"


def classify_candidate(
    pipeline, image, proposed: str, *,
    trust_conformal: bool = True, conf_high: float = 0.85,
) -> tuple[str, str | None, float | None, list[str]]:
    """Return ``(status, model_pred, model_conf, conformal_classes)``."""
    try:
        res = pipeline.analyze(image)
    except Exception as e:  # noqa: BLE001 - never let auto-labeling drop a candidate
        logger.warning("auto-label failed (%s) — routing to human", e)
        return "pending", None, None, []

    top1 = res.get("class_name")
    conf = float(res.get("confidence") or 0.0)
    pset = res.get("prediction_set") or {}
    classes = list(pset.get("classes") or []) if isinstance(pset, dict) else []

    # Not a plant (OOD / detector gate) — a disease candidate that the model
    # reads as not-a-plant is junk: reject it.
    if top1 == "not_a_plant" and proposed != "not_a_plant":
        return "auto_no", top1, conf, classes

    if trust_conformal and classes:
        if classes == [proposed]:                 # calibrated singleton == proposed
            return "auto_yes", top1, conf, classes
        if proposed in classes:                   # multi-class set incl. proposed
            return "pending", top1, conf, classes
        # proposed excluded from the set
        if top1 != proposed and conf >= conf_high:
            return "auto_no", top1, conf, classes
        return "pending", top1, conf, classes

    # Fallback: raw calibrated confidence (no/disabled conformal).
    if top1 == proposed and conf >= conf_high:
        return "auto_yes", top1, conf, classes
    if top1 != proposed and conf >= conf_high:
        return "auto_no", top1, conf, classes
    return "pending", top1, conf, classes
