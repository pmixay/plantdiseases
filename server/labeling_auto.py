"""Model-trust routing for recorded production scans.

``route_scan`` decides, from the already-computed analyze result (conformal
prediction set primary, temperature-calibrated confidence fallback), whether a
recorded scan is confidently self-consistent (``auto_yes``) or suspicious
(``pending`` → the human admin queue). See ``docs/DATASET_LABELING_PLAN.md``.
"""

from __future__ import annotations


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
